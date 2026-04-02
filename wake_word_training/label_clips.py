#!/usr/bin/env python3
"""
Terminal-based labeling tool for wake word audio clips.

Plays each WAV file and prompts the user to label it as positive (y) or negative (n).
Saves labels to labels.csv and supports resuming from where you left off.

Usage:
    python label_clips.py
    python label_clips.py --input ~/wake_word_training_data --output labels.csv
"""

import argparse
import csv
import os
import sys
from pathlib import Path

try:
    import sounddevice as sd
    import soundfile as sf
except ImportError:
    print("Required packages: pip install sounddevice soundfile")
    sys.exit(1)


def load_existing_labels(csv_path: Path) -> dict:
    """Load already-labeled files from CSV."""
    labels = {}
    if csv_path.exists():
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                labels[row["filename"]] = row
    return labels


SKIP_DIRS = {"manual_recordings", "audio_augmented", "model"}


def find_wav_files(input_dir: Path) -> list:
    """Find all WAV files recursively, sorted by name. Skips non-clip directories."""
    wavs = sorted(
        w for w in input_dir.rglob("*.wav")
        if not any(part in SKIP_DIRS for part in w.relative_to(input_dir).parts)
    )


def play_audio(wav_path: Path):
    """Play a WAV file using sounddevice."""
    data, samplerate = sf.read(str(wav_path))
    sd.play(data, samplerate)
    sd.wait()


def label_clips(input_dir: str, output_csv: str):
    input_path = Path(input_dir)
    csv_path = Path(output_csv)

    if not input_path.exists():
        print(f"Error: Input directory does not exist: {input_path}")
        sys.exit(1)

    wav_files = find_wav_files(input_path)
    if not wav_files:
        print(f"No WAV files found in {input_path}")
        sys.exit(1)

    existing = load_existing_labels(csv_path)
    unlabeled = [w for w in wav_files if str(w.relative_to(input_path)) not in existing]

    total = len(wav_files)
    labeled_count = len(existing)
    positive_count = sum(1 for v in existing.values() if v.get("label") == "positive")
    negative_count = sum(1 for v in existing.values() if v.get("label") == "negative")

    print(f"\nFound {total} WAV files, {labeled_count} already labeled ({positive_count} positive, {negative_count} negative)")
    print(f"{len(unlabeled)} remaining to label\n")

    if not unlabeled:
        print("All clips are already labeled!")
        return

    # Open CSV for appending
    write_header = not csv_path.exists()
    csv_file = open(csv_path, "a", newline="")
    writer = csv.DictWriter(csv_file, fieldnames=["filename", "label", "notes"])
    if write_header:
        writer.writeheader()

    print("Controls: y=positive (hey whiz), n=negative, r=replay, s=skip, q=quit\n")

    try:
        for i, wav_path in enumerate(unlabeled):
            rel_path = str(wav_path.relative_to(input_path))
            labeled_count = len(existing)
            print(f"[{labeled_count + 1}/{total}] {rel_path}")
            print(f"  Progress: {positive_count} positive, {negative_count} negative")

            # Play audio
            try:
                play_audio(wav_path)
            except Exception as e:
                print(f"  Error playing audio: {e}")
                continue

            while True:
                choice = input("  Label (y/n/r/s/q): ").strip().lower()

                if choice == "r":
                    try:
                        play_audio(wav_path)
                    except Exception as e:
                        print(f"  Error replaying: {e}")
                    continue
                elif choice == "y":
                    label = "positive"
                    positive_count += 1
                    break
                elif choice == "n":
                    label = "negative"
                    negative_count += 1
                    break
                elif choice == "s":
                    label = None
                    break
                elif choice == "q":
                    print(f"\nStopped. {labeled_count} labeled so far.")
                    csv_file.close()
                    return
                else:
                    print("  Invalid input. Use y/n/r/s/q")

            if label:
                notes = ""
                row = {"filename": rel_path, "label": label, "notes": notes}
                writer.writerow(row)
                csv_file.flush()
                existing[rel_path] = row
                print(f"  -> {label}")
            else:
                print("  -> skipped")

            print()

    except KeyboardInterrupt:
        print(f"\n\nInterrupted. Progress saved to {csv_path}")
    finally:
        csv_file.close()

    labeled_count = len(existing)
    print(f"\nDone! {labeled_count}/{total} labeled ({positive_count} positive, {negative_count} negative)")
    print(f"Labels saved to: {csv_path}")


def main():
    parser = argparse.ArgumentParser(description="Label wake word audio clips")
    parser.add_argument(
        "--input",
        default=os.path.expanduser("~/wake_word_training_data"),
        help="Directory containing WAV files (default: ~/wake_word_training_data)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV file (default: <input_dir>/labels.csv)",
    )
    args = parser.parse_args()

    output = args.output or os.path.join(args.input, "labels.csv")
    label_clips(args.input, output)


if __name__ == "__main__":
    main()
