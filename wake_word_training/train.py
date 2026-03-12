#!/usr/bin/env python3
"""
Train a tiny CNN classifier for wake word detection.

Features: 80-band mel spectrogram from 3-second 16kHz clips -> (1, 80, 94)
Model: ~15K param CNN exported to ONNX (takes mel spectrogram as input).
Mel spectrogram computation happens on-device (Android).

Usage:
    python train.py
    python train.py --input ~/wake_word_training_data --epochs 200
"""

import argparse
import csv
import os
import sys
from pathlib import Path

import numpy as np

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import Dataset, DataLoader
except ImportError:
    print("Required: pip install torch")
    sys.exit(1)

try:
    import librosa
except ImportError:
    print("Required: pip install librosa")
    sys.exit(1)

try:
    from sklearn.metrics import precision_recall_fscore_support, confusion_matrix
    from sklearn.model_selection import train_test_split
except ImportError:
    print("Required: pip install scikit-learn")
    sys.exit(1)

TARGET_SR = 16000
TARGET_DURATION = 3.0
TARGET_SAMPLES = int(TARGET_SR * TARGET_DURATION)
N_MELS = 80
HOP_LENGTH = 512
N_FFT = 1024


def extract_mel_spectrogram(audio: np.ndarray) -> np.ndarray:
    """Extract log-mel spectrogram from raw audio."""
    mel = librosa.feature.melspectrogram(
        y=audio, sr=TARGET_SR, n_mels=N_MELS,
        n_fft=N_FFT, hop_length=HOP_LENGTH
    )
    log_mel = librosa.power_to_db(mel, ref=np.max)
    # Normalize to [0, 1]
    min_val = log_mel.min()
    max_val = log_mel.max()
    log_mel = (log_mel - min_val) / (max_val - min_val + 1e-8)
    return log_mel  # shape: (80, T) where T = ceil(TARGET_SAMPLES / HOP_LENGTH) + 1


def load_audio(path: str) -> np.ndarray:
    """Load and preprocess audio file."""
    try:
        import soundfile as sf
        audio, sr = sf.read(path)
    except Exception:
        audio, sr = librosa.load(path, sr=TARGET_SR)
        return _pad_trim(audio)

    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != TARGET_SR:
        audio = librosa.resample(audio, orig_sr=sr, target_sr=TARGET_SR)
    return _pad_trim(audio)


def _pad_trim(audio: np.ndarray) -> np.ndarray:
    if len(audio) < TARGET_SAMPLES:
        audio = np.pad(audio, (0, TARGET_SAMPLES - len(audio)))
    else:
        audio = audio[:TARGET_SAMPLES]
    return audio.astype(np.float32)


class WakeWordDataset(Dataset):
    def __init__(self, features, labels, training=False):
        self.features = features
        self.labels = labels
        self._training = training

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        # Add channel dimension: (80, T) -> (1, 80, T)
        feat = torch.FloatTensor(self.features[idx]).unsqueeze(0)

        if self._training:
            feat = self._spec_augment(feat)

        return feat, torch.FloatTensor([self.labels[idx]])

    def _spec_augment(self, spec):
        """Apply SpecAugment: frequency and time masking."""
        _, n_freq, n_time = spec.shape

        # Frequency masking
        f = min(10, n_freq // 4)
        if f > 0:
            f0 = np.random.randint(0, max(1, n_freq - f))
            spec[:, f0:f0 + f, :] = 0

        # Time masking
        t = min(10, n_time // 4)
        if t > 0:
            t0 = np.random.randint(0, max(1, n_time - t))
            spec[:, :, t0:t0 + t] = 0

        return spec


class WakeWordClassifier(nn.Module):
    """CNN for wake word classification.

    Input: mel spectrogram (batch, 1, 80, T)
    Output: logit (batch, 1) — NO sigmoid, use BCEWithLogitsLoss.
    """

    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 32, kernel_size=3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Dropout2d(0.2),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Dropout2d(0.2),
            nn.Conv2d(64, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d(1),
        )
        self.classifier = nn.Sequential(
            nn.Dropout(0.3),
            nn.Linear(64, 1),
        )

    def forward(self, x):
        # x: (batch, 1, n_mels, n_time)
        x = self.features(x)
        x = x.view(x.size(0), -1)
        x = self.classifier(x)
        return x  # raw logits


class WakeWordClassifierForExport(nn.Module):
    """Wrapper that adds sigmoid for ONNX export (inference mode)."""

    def __init__(self, model: WakeWordClassifier):
        super().__init__()
        self.model = model

    def forward(self, x):
        return torch.sigmoid(self.model(x))


def load_dataset(input_dir: str):
    """Load labeled dataset and extract features."""
    input_path = Path(input_dir)
    labels_csv = input_path / "labels.csv"

    if not labels_csv.exists():
        print(f"Error: {labels_csv} not found. Run label_clips.py first.")
        sys.exit(1)

    features = []
    labels = []
    filenames = []
    skipped = 0

    with open(labels_csv) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Loading {len(rows)} labeled clips...")

    for row in rows:
        label_str = row.get("label", "").strip()
        if label_str == "positive":
            label = 1.0
        elif label_str == "negative":
            label = 0.0
        else:
            skipped += 1
            continue

        wav_path = input_path / row["filename"]
        if not wav_path.exists():
            print(f"  Missing: {row['filename']}")
            skipped += 1
            continue

        try:
            audio = load_audio(str(wav_path))
            mel = extract_mel_spectrogram(audio)
            features.append(mel)
            labels.append(label)
            filenames.append(row["filename"])
        except Exception as e:
            print(f"  Error processing {row['filename']}: {e}")
            skipped += 1

    if skipped:
        print(f"  Skipped {skipped} clips (missing files or invalid labels)")

    return np.array(features), np.array(labels), filenames


def train(input_dir: str, epochs: int, output_dir: str):
    features, labels, filenames = load_dataset(input_dir)

    n_positive = int(labels.sum())
    n_negative = len(labels) - n_positive
    print(f"\nDataset: {len(labels)} total ({n_positive} positive, {n_negative} negative)")
    print(f"Feature shape: {features[0].shape}")

    if n_positive == 0 or n_negative == 0:
        print("Error: Need both positive and negative samples!")
        sys.exit(1)

    # Split on original clips to avoid data leakage from augmentation.
    # Augmented clips (in audio_augmented/) share the same base stem as their
    # source, so we group by source and split at the group level.
    from collections import defaultdict
    groups = defaultdict(list)  # source_key -> list of indices
    for i, fn in enumerate(filenames):
        # Original clips live outside audio_augmented/; augmented clips start
        # with the original stem.  Derive a source key from the filename.
        base = Path(fn).stem
        # Strip augmentation suffixes added by augment script
        for suffix in ['_p+1', '_p+2', '_p-1', '_p-2',
                        '_s0.85', '_s1.15', '_n5db', '_n10db']:
            if base.endswith(suffix):
                base = base[:-len(suffix)]
                break
        groups[base].append(i)

    group_keys = sorted(groups.keys())
    group_labels_map = {k: labels[groups[k][0]] for k in group_keys}

    # Stratified split at group level
    pos_keys = [k for k in group_keys if group_labels_map[k] == 1.0]
    neg_keys = [k for k in group_keys if group_labels_map[k] == 0.0]
    np.random.seed(42)
    np.random.shuffle(pos_keys)
    np.random.shuffle(neg_keys)
    n_pos_test = max(1, int(len(pos_keys) * 0.2))
    n_neg_test = max(1, int(len(neg_keys) * 0.2))
    test_keys = set(pos_keys[:n_pos_test] + neg_keys[:n_neg_test])
    train_keys = set(group_keys) - test_keys

    train_idx = [i for k in train_keys for i in groups[k]]
    test_idx = [i for k in test_keys for i in groups[k]]

    X_train = features[train_idx]
    X_test = features[test_idx]
    y_train = labels[train_idx]
    y_test = labels[test_idx]
    print(f"Train: {len(y_train)} ({int(y_train.sum())} pos), Test: {len(y_test)} ({int(y_test.sum())} pos)")
    print(f"  (split by {len(train_keys)} train groups, {len(test_keys)} test groups — no augmentation leakage)")

    train_dataset = WakeWordDataset(X_train, y_train, training=True)
    test_dataset = WakeWordDataset(X_test, y_test, training=False)

    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)

    # Model
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = WakeWordClassifier().to(device)

    total_params = sum(p.numel() for p in model.parameters())
    print(f"\nModel parameters: {total_params:,}")

    # BCEWithLogitsLoss with pos_weight to handle class imbalance
    # Model outputs raw logits (no sigmoid), loss applies sigmoid internally
    pos_weight = torch.FloatTensor([n_negative / max(n_positive, 1)]).to(device)
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    print(f"pos_weight: {pos_weight.item():.2f}")

    optimizer = optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=15, factor=0.5, min_lr=1e-5)

    # Training
    best_f1 = 0.0
    best_model_state = None
    no_improve_count = 0

    for epoch in range(epochs):
        model.train()
        train_loss = 0.0

        for batch_x, batch_y in train_loader:
            batch_x, batch_y = batch_x.to(device), batch_y.to(device)
            optimizer.zero_grad()
            output = model(batch_x)
            loss = criterion(output, batch_y)
            loss.backward()
            optimizer.step()
            train_loss += loss.item()

        train_loss /= len(train_loader)

        # Evaluate every 5 epochs
        if (epoch + 1) % 5 == 0 or epoch == epochs - 1:
            model.eval()
            all_preds = []
            all_probs = []
            all_labels = []

            with torch.no_grad():
                for batch_x, batch_y in test_loader:
                    batch_x = batch_x.to(device)
                    output = model(batch_x)
                    probs = torch.sigmoid(output)
                    preds = (probs > 0.5).float()
                    all_probs.extend(probs.cpu().numpy().flatten())
                    all_preds.extend(preds.cpu().numpy().flatten())
                    all_labels.extend(batch_y.numpy().flatten())

            precision, recall, f1, _ = precision_recall_fscore_support(
                all_labels, all_preds, average="binary", zero_division=0
            )

            lr = optimizer.param_groups[0]['lr']
            print(f"Epoch {epoch + 1}/{epochs} - loss: {train_loss:.4f} - "
                  f"P: {precision:.3f} R: {recall:.3f} F1: {f1:.3f} - lr: {lr:.6f}")

            scheduler.step(1 - f1)

            if f1 > best_f1:
                best_f1 = f1
                best_model_state = {k: v.clone() for k, v in model.state_dict().items()}
                no_improve_count = 0
            else:
                no_improve_count += 1

            # Early stopping
            if no_improve_count >= 10:
                print(f"Early stopping at epoch {epoch + 1} (no improvement for {no_improve_count * 5} epochs)")
                break

    # Load best model
    if best_model_state:
        model.load_state_dict(best_model_state)
        print(f"\nBest F1: {best_f1:.3f}")

    # Final evaluation
    model.eval()
    all_preds = []
    all_probs = []
    all_labels = []

    with torch.no_grad():
        for batch_x, batch_y in test_loader:
            batch_x = batch_x.to(device)
            output = model(batch_x)
            probs = torch.sigmoid(output)
            all_probs.extend(probs.cpu().numpy().flatten())
            preds = (probs > 0.5).float()
            all_preds.extend(preds.cpu().numpy().flatten())
            all_labels.extend(batch_y.numpy().flatten())

    precision, recall, f1, _ = precision_recall_fscore_support(
        all_labels, all_preds, average="binary", zero_division=0
    )
    cm = confusion_matrix(all_labels, all_preds)

    print(f"\n{'='*50}")
    print(f"Final Test Results:")
    print(f"  Precision: {precision:.3f}")
    print(f"  Recall:    {recall:.3f}")
    print(f"  F1 Score:  {f1:.3f}")
    print(f"\nConfusion Matrix:")
    print(f"  TN={int(cm[0][0])}, FP={int(cm[0][1])}")
    print(f"  FN={int(cm[1][0])}, TP={int(cm[1][1])}")

    # Print probability distribution
    pos_probs = [p for p, l in zip(all_probs, all_labels) if l == 1.0]
    neg_probs = [p for p, l in zip(all_probs, all_labels) if l == 0.0]
    print(f"\nProbability distribution:")
    print(f"  Positives: mean={np.mean(pos_probs):.3f}, min={np.min(pos_probs):.3f}, max={np.max(pos_probs):.3f}")
    print(f"  Negatives: mean={np.mean(neg_probs):.3f}, min={np.min(neg_probs):.3f}, max={np.max(neg_probs):.3f}")
    print(f"{'='*50}")

    # Export to ONNX — mel spectrogram input (NOT raw audio)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    model_cpu = model.cpu()
    export_model = WakeWordClassifierForExport(model_cpu)
    export_model.eval()

    # Input: mel spectrogram (batch, 1, 80, T)
    n_time = features[0].shape[1]  # number of time frames
    dummy_input = torch.randn(1, 1, N_MELS, n_time)
    onnx_path = output_path / "wake_word_classifier.onnx"

    torch.onnx.export(
        export_model,
        dummy_input,
        str(onnx_path),
        input_names=["mel_spectrogram"],
        output_names=["probability"],
        dynamic_axes={
            "mel_spectrogram": {0: "batch"},
            "probability": {0: "batch"},
        },
        opset_version=13,
    )

    onnx_size = onnx_path.stat().st_size
    print(f"\nONNX model saved to: {onnx_path} ({onnx_size / 1024:.1f} KB)")

    # Save mel filterbank for Android to use
    mel_fb = librosa.filters.mel(sr=TARGET_SR, n_fft=N_FFT, n_mels=N_MELS)
    mel_fb_path = output_path / "mel_filterbank.npy"
    np.save(str(mel_fb_path), mel_fb.astype(np.float32))
    print(f"Mel filterbank saved to: {mel_fb_path} ({mel_fb.shape})")

    # Also save as raw floats for easy loading in Android
    mel_fb_raw_path = output_path / "mel_filterbank.bin"
    mel_fb.astype(np.float32).tofile(str(mel_fb_raw_path))
    print(f"Mel filterbank binary saved to: {mel_fb_raw_path}")

    # Save PyTorch model
    pt_path = output_path / "wake_word_classifier.pt"
    torch.save(best_model_state or model.state_dict(), str(pt_path))
    print(f"PyTorch model saved to: {pt_path}")

    # Save preprocessing params for Android
    params = {
        "sample_rate": TARGET_SR,
        "n_fft": N_FFT,
        "hop_length": HOP_LENGTH,
        "n_mels": N_MELS,
        "n_samples": TARGET_SAMPLES,
        "n_time_frames": n_time,
    }
    import json
    params_path = output_path / "preprocessing_params.json"
    with open(params_path, "w") as f:
        json.dump(params, f, indent=2)
    print(f"Preprocessing params saved to: {params_path}")

    # Copy assets to Android
    android_assets = Path(__file__).parent.parent.parent / "whizvoiceapp" / "app" / "src" / "main" / "assets"
    if android_assets.exists():
        import shutil
        for src_name in ["wake_word_classifier.onnx", "mel_filterbank.bin", "preprocessing_params.json"]:
            src = output_path / src_name
            dest = android_assets / src_name
            shutil.copy2(str(src), str(dest))
            print(f"Copied {src_name} to Android assets")
    else:
        print(f"\nNote: Copy ONNX model, mel_filterbank.bin, and preprocessing_params.json to Android assets manually")


def main():
    parser = argparse.ArgumentParser(description="Train wake word classifier")
    parser.add_argument(
        "--input",
        default=os.path.expanduser("~/wake_word_training_data"),
        help="Training data directory (default: ~/wake_word_training_data)",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=200,
        help="Number of training epochs (default: 200)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output directory for model (default: <input_dir>/model)",
    )
    args = parser.parse_args()

    output = args.output or os.path.join(args.input, "model")
    train(args.input, args.epochs, output)


if __name__ == "__main__":
    main()
