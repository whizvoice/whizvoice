# whizvoice — wake word training playbook

Conventions for retraining the wake word classifier. Update this file whenever the recipe changes.

## Pipeline

1. **Pull new clips:** `python export_wake_word_audio.py` — appends to `~/wake_word_training_data/metadata.csv`, organizes WAVs into `YYYY-MM-DD/` subdirs, deletes from Supabase after successful download. Failed downloads stay in cloud and retry next run.
2. **Label:** `python wake_word_training/label_clips.py --input ~/wake_word_training_data` — interactive `y/n/r/s/q`, resumes from `labels.csv` (matched by filename), CRLF line endings.
3. **Train:** `python wake_word_training/train.py` — defaults below. Model auto-copies into `whizvoiceapp/app/src/main/assets/` (which IS git-tracked, so revert via `git checkout` if regression).

## Training defaults (last reviewed 2026-04-25)

- **Epochs:** 200 with early stopping (patience=10 evals × 5 epochs = 50 epoch window without F1 improvement)
- **Batch size:** 32
- **Optimizer:** Adam, lr=1e-3, weight_decay=1e-4; ReduceLROnPlateau scheduler (patience=15, factor=0.5)
- **Loss:** `BCEWithLogitsLoss` with `pos_weight = n_negative / n_positive` — handles class imbalance at the gradient level, so we do NOT need to augment-to-balance
- **Split:** 80/20 stratified at the *group* level (augmented clips share a group with their source clip — prevents data leakage)
- **Features:** 80-band log-mel spectrogram, 16kHz / 3s clips, n_fft=1024, hop=512 → shape (1, 80, 94)

## Augmentation policy

**Keep the 880 existing augmented clips in `labels.csv`. Don't generate new augmentations.** Confirmed by ablation 2026-04-30:

| Run | Augmented in labels.csv | seed-42 F1 |
|-----|-------------------------|-----------|
| With augmented | 880 | 0.836 |
| Without augmented | 0 | **0.382** |

Removing the 880 augmented entries dropped F1 by 0.454. With only 206 real positives, the CNN doesn't have enough positive signal to train — loss climbs, F1 oscillates 0.000–0.382, probability distributions for pos/neg overlap (means 0.415 vs 0.299). Augmented clips, despite being 8x clones of ~110 source clips, give the model enough variety to learn a usable wake-word feature.

We don't run `augment_data.py` again either — augmenting more old positives won't help and just compounds overfit risk. If a future round wants to try, augment the *new* real positives instead.

## Always train on full labels.csv

Don't drop subsets at training time (no `--exclude-augmented` flag, no manual filtering). If a subset shouldn't be in training, decide that, remove it from `labels.csv`, and commit the removal. Training reads `labels.csv` whole.

## Asset overwrite — commit BEFORE the next retrain

`train.py` (lines ~466-473) auto-copies these into `whizvoiceapp/app/src/main/assets/`:
- `wake_word_classifier.onnx`
- `mel_filterbank.bin`
- `preprocessing_params.json`

It also overwrites `~/wake_word_training_data/model/wake_word_classifier.{onnx,pt}` without backup.

**Lesson from 2026-04-30:** Apr-25 model was never committed and got overwritten by Apr-30 training, so we couldn't run `eval_compare.py` against it later. Workflow:
1. Before retraining, `git -C whizvoiceapp commit -m "..."` the previous round's `wake_word_classifier.onnx`. That preserves it for future `eval_compare.py` regression checks.
2. After training, `git diff` to sanity-check the new asset, then commit when you're satisfied.
3. If the new model is worse: `git checkout HEAD -- app/src/main/assets/wake_word_classifier.onnx` reverts to the previous committed model.

## Per-round notes

| Date | Total labels | Pos / Neg | Real pos | Augmented pos | F1† | P / R† | Notes |
|------|--------------|-----------|----------|---------------|-----|--------|-------|
| 2026-03-11 | — | — | — | 880 generated | — | — | First augmentation round |
| 2026-04-08 | ~1549 | 1008 / 541 | 128 | 880 | 0.743 | 0.648 / 0.871 | Original metrics not recorded; numbers above are retro-eval on 2026-04-25 seed-42 split via `eval_compare.py`. Commit `a9ab4c3d`. |
| 2026-04-25 | 2828 | 1086 / 1742 | 206 | 880 | 0.787 | 0.952 / 0.670 | Loss climbed after epoch 20, F1 oscillated — investigate lr/scheduler next round. **ONNX lost** (overwritten by Apr-30 retrain before commit). |
| 2026-04-30 (no-aug ablation) | 1948 | 206 / 1742 | 206 | 0 | 0.382 | 0.481 / 0.317 | Ablation: trained without augmented clips. F1 collapsed; pos_weight=8.46 didn't compensate for too-few real positives. Confirmed augmented clips are load-bearing. |
| 2026-04-30 (final) | 2828 | 1086 / 1742 | 206 | 880 | 0.836 | 0.870 / 0.804 | Same dataset as Apr-25 except 8 sleepy labels were re-labeled & flipped. F1 +0.049 vs Apr-25; recent-only F1 doubled (0.103 → 0.207). Best model to date. |

†Metrics on train.py's seed-42 group-stratified test split of that round's labels.csv. Append a row each retrain after reading train.py's "Final Test Results" block.

### Latest comparison: Apr-08 vs Apr-30 (via `eval_compare.py` 2026-04-30)

Apr-25 ONNX is unrecoverable (overwritten before commit), so the head-to-head is Apr-08 vs Apr-30. Apr-25 numbers shown for context from the per-round table.

**seed-42 test split (557 clips, 209 positive):**

| Model | P | R | F1 | pos_mean | neg_mean |
|-------|---|---|----|----------|----------|
| Apr-08 | 0.652 | 0.880 | 0.749 | 0.883 | 0.315 |
| Apr-25 (lost) | 0.952 | 0.670 | 0.787 | 0.661 | 0.023 |
| **Apr-30** | **0.870** | **0.804** | **0.836** | 0.804 | 0.124 |

**recent-only (1279 clips Apr 9+, 78 positive):**

| Model | P | R | F1 |
|-------|---|---|----|
| Apr-08 | 0.063 | 0.346 | 0.107 |
| Apr-25 (lost) | 0.154 | 0.077 | 0.103 |
| **Apr-30** | **0.168** | **0.269** | **0.207** |

**today's-307 clean labels (23 positive):**

| Model | P | R | F1 |
|-------|---|---|----|
| Apr-08 | 0.109 | 0.304 | 0.161 |
| Apr-25 (lost) | 0.000 | 0.000 | 0.000 |
| **Apr-30** | **0.167** | **0.217** | **0.189** |

Headline: Apr-30 is the best model in every view. Recent-only F1 doubled vs Apr-25 (0.103 → 0.207) despite only 8 labels actually changing — likely random-init variance in the 56K-param CNN (training script doesn't seed `torch.manual_seed`). Recent-only F1 is still bad in absolute terms (0.207), so generalization to new acoustic conditions remains the next thing to chip at.

Action items for next round:
1. Set `torch.manual_seed(42)` in `train.py` so retrains are reproducible. Random variance is currently masking real signal.
2. Investigate the loss-climbs-after-epoch-20 pattern — try lr=3e-4 or stronger regularization.
3. Consider weighing recent positives higher (sample weight or oversampling) since recent-only F1=0.207 means the model still doesn't fit current acoustic conditions well.

## Tools

- `wake_word_training/eval_compare.py` — compare ONNX models on the same held-out test sets. Runs both seed-42 split AND recent-only views. Use after each retrain to verify the new model didn't regress on truly new data:
  ```
  git -C ../whizvoiceapp show <PRIOR_COMMIT>:app/src/main/assets/wake_word_classifier.onnx > /tmp/old.onnx
  python wake_word_training/eval_compare.py --models /tmp/old.onnx:Old ~/wake_word_training_data/model/wake_word_classifier.onnx:New
  ```
