#!/usr/bin/env python3
"""Compare two ONNX wake word models on the same held-out clips.

Reports two views:
  1. seed-42 test split — same split train.py uses for the new model's reported metrics
  2. recent-only — clips dated >= --recent-cutoff (default 2026-04-09), held out from any
     pre-Apr-9 model and useful for "did retraining help on new data" questions

Usage:
  python eval_compare.py --models /tmp/old.onnx:Apr-08 ../path/new.onnx:Apr-25
"""
import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import librosa
import soundfile as sf
import onnxruntime as ort
from sklearn.metrics import precision_recall_fscore_support, confusion_matrix

TARGET_SR = 16000
TARGET_SAMPLES = int(TARGET_SR * 3.0)
N_MELS = 80
HOP_LENGTH = 512
N_FFT = 1024
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})/")


def load_audio(path: Path) -> np.ndarray:
    audio, sr = sf.read(str(path))
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != TARGET_SR:
        audio = librosa.resample(audio, orig_sr=sr, target_sr=TARGET_SR)
    if len(audio) < TARGET_SAMPLES:
        audio = np.pad(audio, (0, TARGET_SAMPLES - len(audio)))
    else:
        audio = audio[:TARGET_SAMPLES]
    return audio.astype(np.float32)


def extract_mel(audio: np.ndarray) -> np.ndarray:
    mel = librosa.feature.melspectrogram(
        y=audio, sr=TARGET_SR, n_mels=N_MELS, n_fft=N_FFT, hop_length=HOP_LENGTH
    )
    log_mel = librosa.power_to_db(mel, ref=np.max)
    mn, mx = log_mel.min(), log_mel.max()
    return (log_mel - mn) / (mx - mn + 1e-8)


def load_labeled_rows(input_path: Path):
    rows = []
    with open(input_path / "labels.csv") as f:
        for row in csv.DictReader(f):
            ls = row.get("label", "").strip()
            if ls == "positive":
                label = 1.0
            elif ls == "negative":
                label = 0.0
            else:
                continue
            wav = input_path / row["filename"]
            if not wav.exists():
                continue
            rows.append((row["filename"], label))
    return rows


def seed42_test_indices(rows):
    """Reproduce train.py's group-stratified seed-42 split."""
    groups = defaultdict(list)
    for i, (fn, _) in enumerate(rows):
        base = Path(fn).stem
        for s in ("_p+1", "_p+2", "_p-1", "_p-2", "_s0.85", "_s1.15", "_n5db", "_n10db"):
            if base.endswith(s):
                base = base[: -len(s)]
                break
        groups[base].append(i)
    keys = sorted(groups.keys())
    label_map = {k: rows[groups[k][0]][1] for k in keys}
    pos = [k for k in keys if label_map[k] == 1.0]
    neg = [k for k in keys if label_map[k] == 0.0]
    np.random.seed(42)
    np.random.shuffle(pos)
    np.random.shuffle(neg)
    n_pos = max(1, int(len(pos) * 0.2))
    n_neg = max(1, int(len(neg) * 0.2))
    test_keys = set(pos[:n_pos] + neg[:n_neg])
    return [i for k in test_keys for i in groups[k]]


def recent_indices(rows, cutoff: str):
    out = []
    for i, (fn, _) in enumerate(rows):
        m = DATE_RE.match(fn)
        if m and m.group(1) >= cutoff:
            out.append(i)
    return out


def evaluate(name, onnx_path, features, labels):
    sess = ort.InferenceSession(str(onnx_path))
    in_name = sess.get_inputs()[0].name
    probs = []
    for feat in features:
        x = feat[np.newaxis, np.newaxis, :, :].astype(np.float32)
        out = sess.run(None, {in_name: x})[0]
        probs.append(float(out.flatten()[0]))
    probs = np.array(probs)
    preds = (probs > 0.5).astype(float)
    p, r, f1, _ = precision_recall_fscore_support(
        labels, preds, average="binary", zero_division=0
    )
    cm = confusion_matrix(labels, preds)
    pos_p = probs[labels == 1.0]
    neg_p = probs[labels == 0.0]
    print(f"  {name:20s} P={p:.3f} R={r:.3f} F1={f1:.3f}  "
          f"TN={cm[0][0]} FP={cm[0][1]} FN={cm[1][0]} TP={cm[1][1]}  "
          f"pos_mean={pos_p.mean():.3f} neg_mean={neg_p.mean():.3f}")
    return {"precision": p, "recall": r, "f1": f1, "cm": cm}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="~/wake_word_training_data")
    parser.add_argument("--models", nargs="+", required=True,
                        help="path:label pairs, e.g. /tmp/old.onnx:Apr-08")
    parser.add_argument("--recent-cutoff", default="2026-04-09",
                        help="ISO date — clips in folders >= this are 'recent'")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser()
    rows = load_labeled_rows(input_path)
    print(f"Loaded {len(rows)} labeled clips with files on disk")

    test_idx = seed42_test_indices(rows)
    recent_idx = recent_indices(rows, args.recent_cutoff)
    eval_idx = sorted(set(test_idx) | set(recent_idx))

    print(f"Computing mel features for {len(eval_idx)} clips "
          f"(seed-42 test={len(test_idx)}, recent={len(recent_idx)}, union={len(eval_idx)})...")
    feature_cache = {}
    for i in eval_idx:
        fn, _ = rows[i]
        feature_cache[i] = extract_mel(load_audio(input_path / fn))

    test_feats = np.array([feature_cache[i] for i in test_idx])
    test_labels = np.array([rows[i][1] for i in test_idx])
    recent_feats = np.array([feature_cache[i] for i in recent_idx])
    recent_labels = np.array([rows[i][1] for i in recent_idx])

    models = []
    for spec in args.models:
        path, name = spec.split(":", 1) if ":" in spec else (spec, Path(spec).stem)
        models.append((name, Path(path).expanduser()))

    print(f"\n--- seed-42 test split ({len(test_idx)} clips, "
          f"{int(test_labels.sum())} positive) ---")
    print(f"  Caveat: this set was carved from today's labels.csv with seed 42. "
          f"For pre-today models, some test clips were in their train set.")
    for name, path in models:
        evaluate(name, path, test_feats, test_labels)

    print(f"\n--- recent-only ({len(recent_idx)} clips, "
          f"{int(recent_labels.sum())} positive, dated >= {args.recent_cutoff}) ---")
    print(f"  Held-out from any model trained before {args.recent_cutoff}. "
          f"For models trained on this data, this is training-set evaluation.")
    for name, path in models:
        evaluate(name, path, recent_feats, recent_labels)


if __name__ == "__main__":
    main()
