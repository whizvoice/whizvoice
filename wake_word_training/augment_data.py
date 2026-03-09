#!/usr/bin/env python3
"""
Augment positive wake word samples to balance the training dataset.

Sources:
1. TTS synthesis using macOS `say` command with multiple voices
2. Audio augmentation (pitch shift, speed change, noise overlay, volume normalization)

Usage:
    python augment_data.py
    python augment_data.py --input ~/wake_word_training_data --target 200
"""

import argparse
import csv
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

try:
    import soundfile as sf
except ImportError:
    print("Required: pip install soundfile")
    sys.exit(1)

try:
    import librosa
except ImportError:
    print("Required: pip install librosa")
    sys.exit(1)

TARGET_SR = 16000
TARGET_DURATION = 3.0  # seconds
TARGET_SAMPLES = int(TARGET_SR * TARGET_DURATION)

# macOS voices for TTS
MACOS_VOICES = [
    "Samantha", "Alex", "Daniel", "Karen", "Moira",
    "Rishi", "Tessa", "Veena", "Fiona", "Victoria",
]

PHRASE = "hey whiz"


def load_and_pad(path: str, sr: int = TARGET_SR) -> np.ndarray:
    """Load audio, resample to target SR, pad/trim to target duration."""
    audio, orig_sr = sf.read(path)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if orig_sr != sr:
        audio = librosa.resample(audio, orig_sr=orig_sr, target_sr=sr)
    # Pad or trim to target length
    if len(audio) < TARGET_SAMPLES:
        audio = np.pad(audio, (0, TARGET_SAMPLES - len(audio)))
    else:
        audio = audio[:TARGET_SAMPLES]
    return audio


def save_wav(audio: np.ndarray, path: str, sr: int = TARGET_SR):
    """Save audio as 16-bit WAV."""
    # Normalize to prevent clipping
    peak = np.abs(audio).max()
    if peak > 0:
        audio = audio / peak * 0.9
    sf.write(path, audio, sr, subtype="PCM_16")


def generate_tts_clips(output_dir: Path, labels_csv: Path) -> int:
    """Generate TTS clips using macOS say command."""
    tts_dir = output_dir / "tts_augmented"
    tts_dir.mkdir(exist_ok=True)

    count = 0
    csv_file = open(labels_csv, "a", newline="")
    writer = csv.DictWriter(csv_file, fieldnames=["filename", "label", "notes"])

    for voice in MACOS_VOICES:
        # Check if voice is available
        result = subprocess.run(
            ["say", "-v", voice, "--file-format=WAVE", "-o", "/dev/null", "test"],
            capture_output=True,
        )
        if result.returncode != 0:
            print(f"  Voice '{voice}' not available, skipping")
            continue

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            # Generate base TTS
            subprocess.run(
                ["say", "-v", voice, "--file-format=WAVE",
                 "--data-format=LEI16@16000", "-o", tmp_path, PHRASE],
                check=True, capture_output=True,
            )

            audio = load_and_pad(tmp_path)

            # Save base version
            base_name = f"tts_{voice.lower()}.wav"
            base_path = tts_dir / base_name
            save_wav(audio, str(base_path))
            rel = str(base_path.relative_to(output_dir))
            writer.writerow({"filename": rel, "label": "positive", "notes": f"tts_{voice}"})
            count += 1

            # Pitch-shifted versions
            for semitones in [-2, -1, 1, 2]:
                shifted = librosa.effects.pitch_shift(audio, sr=TARGET_SR, n_steps=semitones)
                shifted = shifted[:TARGET_SAMPLES]
                name = f"tts_{voice.lower()}_pitch{semitones:+d}.wav"
                path = tts_dir / name
                save_wav(shifted, str(path))
                rel = str(path.relative_to(output_dir))
                writer.writerow({"filename": rel, "label": "positive", "notes": f"tts_{voice}_pitch{semitones:+d}"})
                count += 1

            # Speed-changed versions
            for rate in [0.85, 1.15]:
                stretched = librosa.effects.time_stretch(audio, rate=rate)
                stretched = load_and_pad_array(stretched)
                name = f"tts_{voice.lower()}_speed{rate:.2f}.wav"
                path = tts_dir / name
                save_wav(stretched, str(path))
                rel = str(path.relative_to(output_dir))
                writer.writerow({"filename": rel, "label": "positive", "notes": f"tts_{voice}_speed{rate}"})
                count += 1

        except subprocess.CalledProcessError as e:
            print(f"  TTS failed for voice '{voice}': {e}")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    csv_file.close()
    return count


def load_and_pad_array(audio: np.ndarray) -> np.ndarray:
    """Pad or trim an audio array to target length."""
    if len(audio) < TARGET_SAMPLES:
        audio = np.pad(audio, (0, TARGET_SAMPLES - len(audio)))
    else:
        audio = audio[:TARGET_SAMPLES]
    return audio


def load_negative_samples(input_dir: Path, labels_csv: Path) -> list:
    """Load negative sample audio for noise overlay."""
    negatives = []
    if not labels_csv.exists():
        return negatives
    with open(labels_csv, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("label") == "negative":
                path = input_dir / row["filename"]
                if path.exists():
                    try:
                        audio = load_and_pad(str(path))
                        negatives.append(audio)
                    except Exception:
                        pass
                if len(negatives) >= 50:  # Cap to avoid loading too many
                    break
    return negatives


def augment_positive_clips(
    input_dir: Path, labels_csv: Path, negative_samples: list
) -> int:
    """Apply audio augmentation to existing positive clips."""
    aug_dir = input_dir / "audio_augmented"
    aug_dir.mkdir(exist_ok=True)

    # Find existing positive clips
    positives = []
    if labels_csv.exists():
        with open(labels_csv, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("label") == "positive":
                    path = input_dir / row["filename"]
                    if path.exists():
                        positives.append((row["filename"], str(path)))

    if not positives:
        print("  No positive clips to augment")
        return 0

    csv_file = open(labels_csv, "a", newline="")
    writer = csv.DictWriter(csv_file, fieldnames=["filename", "label", "notes"])
    count = 0

    for orig_name, orig_path in positives:
        try:
            audio = load_and_pad(orig_path)
        except Exception as e:
            print(f"  Error loading {orig_name}: {e}")
            continue

        base = Path(orig_name).stem

        # 1. Pitch shift variants
        for semitones in [-2, -1, 1, 2]:
            shifted = librosa.effects.pitch_shift(audio, sr=TARGET_SR, n_steps=semitones)
            shifted = shifted[:TARGET_SAMPLES]
            name = f"{base}_pitch{semitones:+d}.wav"
            path = aug_dir / name
            save_wav(shifted, str(path))
            rel = str(path.relative_to(input_dir))
            writer.writerow({"filename": rel, "label": "positive", "notes": f"pitch_shift_{semitones:+d}_from_{orig_name}"})
            count += 1

        # 2. Speed change
        for rate in [0.85, 1.15]:
            stretched = librosa.effects.time_stretch(audio, rate=rate)
            stretched = load_and_pad_array(stretched)
            name = f"{base}_speed{rate:.2f}.wav"
            path = aug_dir / name
            save_wav(stretched, str(path))
            rel = str(path.relative_to(input_dir))
            writer.writerow({"filename": rel, "label": "positive", "notes": f"speed_{rate}_from_{orig_name}"})
            count += 1

        # 3. Background noise overlay (mix with random negative sample)
        if negative_samples:
            noise = random.choice(negative_samples)
            for snr_db in [10, 5]:
                noisy = mix_with_noise(audio, noise, snr_db)
                name = f"{base}_noise{snr_db}db.wav"
                path = aug_dir / name
                save_wav(noisy, str(path))
                rel = str(path.relative_to(input_dir))
                writer.writerow({"filename": rel, "label": "positive", "notes": f"noise_{snr_db}db_from_{orig_name}"})
                count += 1

        # 4. Volume variation
        for gain_db in [-6, 6]:
            gain = 10 ** (gain_db / 20.0)
            varied = audio * gain
            name = f"{base}_vol{gain_db:+d}db.wav"
            path = aug_dir / name
            save_wav(varied, str(path))
            rel = str(path.relative_to(input_dir))
            writer.writerow({"filename": rel, "label": "positive", "notes": f"volume_{gain_db:+d}db_from_{orig_name}"})
            count += 1

    csv_file.close()
    return count


def mix_with_noise(signal: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    """Mix signal with noise at a given SNR."""
    sig_power = np.mean(signal ** 2) + 1e-10
    noise_power = np.mean(noise ** 2) + 1e-10
    snr_linear = 10 ** (snr_db / 10.0)
    noise_scale = np.sqrt(sig_power / (noise_power * snr_linear))
    return signal + noise * noise_scale


def main():
    parser = argparse.ArgumentParser(description="Augment positive wake word samples")
    parser.add_argument(
        "--input",
        default=os.path.expanduser("~/wake_word_training_data"),
        help="Training data directory (default: ~/wake_word_training_data)",
    )
    parser.add_argument(
        "--target",
        type=int,
        default=200,
        help="Target number of positive samples (default: 200)",
    )
    args = parser.parse_args()

    input_dir = Path(args.input)
    labels_csv = input_dir / "labels.csv"

    if not input_dir.exists():
        print(f"Error: Input directory does not exist: {input_dir}")
        sys.exit(1)

    if not labels_csv.exists():
        print(f"Error: No labels.csv found in {input_dir}. Run label_clips.py first.")
        sys.exit(1)

    # Count existing positives
    existing_positives = 0
    with open(labels_csv) as f:
        for row in csv.DictReader(f):
            if row.get("label") == "positive":
                existing_positives += 1
    print(f"Existing positive samples: {existing_positives}")

    # Step 1: Generate TTS clips
    print("\n--- TTS Generation ---")
    tts_count = generate_tts_clips(input_dir, labels_csv)
    print(f"Generated {tts_count} TTS clips")

    # Step 2: Load negative samples for noise overlay
    print("\n--- Loading negative samples for noise overlay ---")
    negatives = load_negative_samples(input_dir, labels_csv)
    print(f"Loaded {len(negatives)} negative samples")

    # Step 3: Augment all positive clips
    print("\n--- Audio Augmentation ---")
    aug_count = augment_positive_clips(input_dir, labels_csv, negatives)
    print(f"Generated {aug_count} augmented clips")

    # Final count
    total_positives = 0
    total_negatives = 0
    with open(labels_csv) as f:
        for row in csv.DictReader(f):
            if row.get("label") == "positive":
                total_positives += 1
            elif row.get("label") == "negative":
                total_negatives += 1

    print(f"\n--- Summary ---")
    print(f"Total positive samples: {total_positives}")
    print(f"Total negative samples: {total_negatives}")
    if total_positives < args.target:
        print(f"Warning: Still below target of {args.target} positives. Consider recording more manually.")


if __name__ == "__main__":
    main()
