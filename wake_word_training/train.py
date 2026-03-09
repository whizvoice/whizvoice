#!/usr/bin/env python3
"""
Train a tiny CNN classifier for wake word detection.

Features: 80-band mel spectrogram from 3-second 16kHz clips -> (1, 80, 94)
Model: ~15K param CNN exported to ONNX with mel spectrogram baked in.

Usage:
    python train.py
    python train.py --input ~/wake_word_training_data --epochs 100
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
    log_mel = (log_mel - log_mel.min()) / (log_mel.max() - log_mel.min() + 1e-8)
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
    def __init__(self, features, labels):
        self.features = features
        self.labels = labels

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        # Add channel dimension: (80, T) -> (1, 80, T)
        feat = torch.FloatTensor(self.features[idx]).unsqueeze(0)

        # SpecAugment: random frequency and time masking during training
        if self.training:
            feat = self._spec_augment(feat)

        return feat, torch.FloatTensor([self.labels[idx]])

    @property
    def training(self):
        return getattr(self, '_training', False)

    @training.setter
    def training(self, val):
        self._training = val

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
    """Tiny CNN for wake word classification (~15K params)."""

    def __init__(self, n_mels=N_MELS, n_time_frames=None):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 16, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Conv2d(16, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Conv2d(32, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d(1),
        )
        self.classifier = nn.Sequential(
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )

    def forward(self, x):
        # x: (batch, 1, n_mels, n_time)
        x = self.features(x)
        x = x.view(x.size(0), -1)
        x = self.classifier(x)
        return x


class WakeWordONNXWrapper(nn.Module):
    """Wrapper that includes mel spectrogram computation for ONNX export.

    Takes raw audio floats as input and outputs classification probability.
    The mel spectrogram is computed using a manual filterbank so it's
    fully traceable by ONNX.
    """

    def __init__(self, classifier: WakeWordClassifier, n_fft=N_FFT,
                 hop_length=HOP_LENGTH, n_mels=N_MELS, sr=TARGET_SR):
        super().__init__()
        self.classifier = classifier
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.n_mels = n_mels

        # Pre-compute mel filterbank as a buffer
        mel_fb = librosa.filters.mel(sr=sr, n_fft=n_fft, n_mels=n_mels)
        self.register_buffer("mel_fb", torch.FloatTensor(mel_fb))

        # Hann window
        self.register_buffer("window", torch.hann_window(n_fft))

    def forward(self, audio: torch.Tensor) -> torch.Tensor:
        """
        Args:
            audio: (batch, n_samples) raw audio float tensor
        Returns:
            (batch, 1) probability
        """
        # STFT
        spec = torch.stft(
            audio, n_fft=self.n_fft, hop_length=self.hop_length,
            window=self.window, return_complex=True
        )
        # Power spectrogram
        power = spec.abs() ** 2  # (batch, n_fft//2+1, T)

        # Mel spectrogram
        mel = torch.matmul(self.mel_fb, power)  # (batch, n_mels, T)

        # Log scale (with floor to avoid log(0))
        log_mel = torch.log10(mel.clamp(min=1e-10))

        # Normalize per-sample
        batch_size = log_mel.size(0)
        for i in range(batch_size):
            sample = log_mel[i]
            min_val = sample.min()
            max_val = sample.max()
            log_mel[i] = (sample - min_val) / (max_val - min_val + 1e-8)

        # Add channel dim: (batch, n_mels, T) -> (batch, 1, n_mels, T)
        log_mel = log_mel.unsqueeze(1)

        return self.classifier(log_mel)


def load_dataset(input_dir: str):
    """Load labeled dataset and extract features."""
    input_path = Path(input_dir)
    labels_csv = input_path / "labels.csv"

    if not labels_csv.exists():
        print(f"Error: {labels_csv} not found. Run label_clips.py first.")
        sys.exit(1)

    features = []
    labels = []
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
            skipped += 1
            continue

        try:
            audio = load_audio(str(wav_path))
            mel = extract_mel_spectrogram(audio)
            features.append(mel)
            labels.append(label)
        except Exception as e:
            print(f"  Error processing {row['filename']}: {e}")
            skipped += 1

    if skipped:
        print(f"  Skipped {skipped} clips (missing files or invalid labels)")

    return np.array(features), np.array(labels)


def train(input_dir: str, epochs: int, output_dir: str):
    features, labels = load_dataset(input_dir)

    n_positive = int(labels.sum())
    n_negative = len(labels) - n_positive
    print(f"\nDataset: {len(labels)} total ({n_positive} positive, {n_negative} negative)")
    print(f"Feature shape: {features[0].shape}")

    if n_positive == 0 or n_negative == 0:
        print("Error: Need both positive and negative samples!")
        sys.exit(1)

    # Stratified train/test split
    X_train, X_test, y_train, y_test = train_test_split(
        features, labels, test_size=0.2, stratify=labels, random_state=42
    )
    print(f"Train: {len(y_train)} ({int(y_train.sum())} pos), Test: {len(y_test)} ({int(y_test.sum())} pos)")

    train_dataset = WakeWordDataset(X_train, y_train)
    test_dataset = WakeWordDataset(X_test, y_test)
    train_dataset.training = True
    test_dataset.training = False

    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=32, shuffle=False)

    # Model
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = WakeWordClassifier(n_mels=N_MELS).to(device)

    total_params = sum(p.numel() for p in model.parameters())
    print(f"\nModel parameters: {total_params:,}")

    # Handle class imbalance with weighted loss
    pos_weight = torch.FloatTensor([n_negative / max(n_positive, 1)]).to(device)
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

    # Since we use Sigmoid in the model, use BCE loss instead
    criterion = nn.BCELoss()
    optimizer = optim.Adam(model.parameters(), lr=1e-3)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=10, factor=0.5)

    # Training
    best_f1 = 0.0
    best_model_state = None

    for epoch in range(epochs):
        model.train()
        train_dataset.training = True
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

        # Evaluate
        if (epoch + 1) % 5 == 0 or epoch == epochs - 1:
            model.eval()
            test_dataset.training = False
            all_preds = []
            all_labels = []

            with torch.no_grad():
                for batch_x, batch_y in test_loader:
                    batch_x = batch_x.to(device)
                    output = model(batch_x)
                    preds = (output > 0.5).float()
                    all_preds.extend(preds.cpu().numpy().flatten())
                    all_labels.extend(batch_y.numpy().flatten())

            precision, recall, f1, _ = precision_recall_fscore_support(
                all_labels, all_preds, average="binary", zero_division=0
            )

            print(f"Epoch {epoch + 1}/{epochs} - loss: {train_loss:.4f} - "
                  f"P: {precision:.3f} R: {recall:.3f} F1: {f1:.3f}")

            scheduler.step(1 - f1)

            if f1 > best_f1:
                best_f1 = f1
                best_model_state = model.state_dict().copy()

    # Load best model
    if best_model_state:
        model.load_state_dict(best_model_state)
        print(f"\nBest F1: {best_f1:.3f}")

    # Final evaluation
    model.eval()
    test_dataset.training = False
    all_preds = []
    all_probs = []
    all_labels = []

    with torch.no_grad():
        for batch_x, batch_y in test_loader:
            batch_x = batch_x.to(device)
            output = model(batch_x)
            all_probs.extend(output.cpu().numpy().flatten())
            preds = (output > 0.5).float()
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
    print(f"{'='*50}")

    # Export to ONNX with mel spectrogram baked in
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    model_cpu = model.cpu()
    wrapper = WakeWordONNXWrapper(model_cpu)
    wrapper.eval()

    # Dummy input: raw audio
    dummy_audio = torch.randn(1, TARGET_SAMPLES)
    onnx_path = output_path / "wake_word_classifier.onnx"

    torch.onnx.export(
        wrapper,
        dummy_audio,
        str(onnx_path),
        input_names=["audio"],
        output_names=["probability"],
        dynamic_axes={
            "audio": {0: "batch"},
            "probability": {0: "batch"},
        },
        opset_version=13,
    )

    onnx_size = onnx_path.stat().st_size
    print(f"\nONNX model saved to: {onnx_path} ({onnx_size / 1024:.1f} KB)")

    # Also save PyTorch model
    pt_path = output_path / "wake_word_classifier.pt"
    torch.save(best_model_state or model.state_dict(), str(pt_path))
    print(f"PyTorch model saved to: {pt_path}")

    # Copy ONNX to Android assets
    android_assets = Path(input_dir).parent / "whizvoiceapp" / "app" / "src" / "main" / "assets"
    if not android_assets.exists():
        # Try relative to script location
        android_assets = Path(__file__).parent.parent.parent / "whizvoiceapp" / "app" / "src" / "main" / "assets"

    if android_assets.exists():
        import shutil
        dest = android_assets / "wake_word_classifier.onnx"
        shutil.copy2(str(onnx_path), str(dest))
        print(f"Copied ONNX model to Android assets: {dest}")
    else:
        print(f"\nNote: Copy {onnx_path} to whizvoiceapp/app/src/main/assets/ manually")


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
        default=100,
        help="Number of training epochs (default: 100)",
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
