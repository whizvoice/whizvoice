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

| Date | Commit | Seed | Total labels | Pos / Neg | Real pos | Augmented pos | F1† | P / R† | Notes |
|------|--------|------|--------------|-----------|----------|---------------|-----|--------|-------|
| 2026-03-11 | `dc80e3f8` | — | — | — | — | 880 generated | — | — | First classifier introduction + first augmentation round. Commit message: "add classifier to wake word detection to prevent false detects". Metrics not recorded; could be retro-eval'd via `git show dc80e3f8:app/src/main/assets/wake_word_classifier.onnx > /tmp/mar11.onnx && eval_compare.py`. |
| 2026-04-08 | `a9ab4c3d` | — | ~1549 | 1008 / 541 | 128 | 880 | 0.743 | 0.648 / 0.871 | Original metrics not recorded; numbers above are retro-eval on 2026-04-25 seed-42 split via `eval_compare.py`. |
| 2026-04-25 | **lost** | — | 2828 | 1086 / 1742 | 206 | 880 | 0.787 | 0.952 / 0.670 | Loss climbed after epoch 20, F1 oscillated. ONNX lost (overwritten by Apr-30 retrain before commit). |
| 2026-04-26 (no-aug ablation) | not committed | — | 1948 | 206 / 1742 | 206 | 0 | 0.382 | 0.481 / 0.317 | Ablation (trained 2026-04-30): no augmented clips. F1 collapsed; pos_weight=8.46 didn't compensate for too-few real positives. Confirmed augmented clips are load-bearing. |
| 2026-04-26 | `b627d204` | — | 2828 | 1086 / 1742 | 206 | 880 | 0.836 | 0.870 / 0.804 | Trained 2026-04-30. Same dataset as Apr-25 except 8 sleepy labels were re-labeled & flipped. F1 +0.049 vs Apr-25; recent-only F1 doubled (0.103 → 0.207). |
| 2026-04-30 | `61314ece` | — | 3212 | 1092 / 2120 | 212 | 880 | 0.829 | 0.857 / 0.802 | Trained 2026-04-30 on full 3212 (incl. fresh 384-batch labels). Slight F1 regression vs Apr-26 (–0.007, within noise); recent-only F1 dropped to 0.169. |
| 2026-05-10 | `a5fc6590` | — | 3254 (filtered to 2972) | 1134 / 2120→1838 | 234 | 880 | 0.718 | 0.855 / 0.619 | First run with `in_training` column — 48 post-Apr-26 positives all in, 96 negatives in (12 hard-neg FPs + 84 random) of 378 available. Recent-only F1=**0.206** (best to date), 384-batch FPs=2 (best by far). **Currently deployed.** |
| 2026-05-11 seed search | not committed | 0, 1, 42, 100, 1234 | 2972 (filter) | 1134 / 1838 | 234 | 880 | 0.642-0.732 | varied | 5-seed sweep with `torch.manual_seed` + `np.random.seed` added at top of `train()`. Recent-only F1 ranged 0.087-0.187. None beat May-10's lucky unseeded run. seed=0 had lowest FPs (11 on 384-batch); seed=1 had best recent F1 (0.187). Conclusion: data is too small for any seed to clearly win. |
| 2026-05-12 | [`ed11a8a`](https://github.com/tsurantino/wakeword-prototype/commit/ed11a8abe8c8491576bae38b51f74dd968dbe991) (ext. repo `tsurantino/wakeword-prototype`) | — | 2280 (author held-out) | 161 / 2119 | — | — | — | — / 0.913 (author) | **External prototype, different architecture.** openWakeWord 3-stage pipeline (`melspectrogram.onnx → embedding_model.onnx → hey_whiz.onnx`), not the in-tree 56K-param CNN. Trained by Artur via LiveKit pipeline (`training/configs/hey_whiz.yaml`, 100k base steps + 2 adaptive phases on negative-weight doubling, max_negative_weight=5000, 50-entry adversarial list). Commit-message eval on author's held-out clips: 91.3% recall / 2.27% FAR @ thr 0.5 (89% recall / 0.76% FAR @ 0.85). **NOT YET re-evaluated** via our `eval_compare.py` — preprocessing differs, so `eval_compare.py` would need a 3-stage inference path before head-to-head with May-10. Local clone at `~/wakeword-prototype`. |

†Metrics on train.py's seed-42 group-stratified test split of that round's labels.csv. Append a row each retrain after reading train.py's "Final Test Results" block. **Always commit `whizvoiceapp/app/src/main/assets/wake_word_classifier.onnx` and record the short hash in the Commit column** so the model can be recovered for `eval_compare.py` regression checks. **Also record the `--seed` value** since `train.py` now accepts that flag (added 2026-05-11). For rows where seed is "—", the seed wasn't recorded (mostly runs before the seeding was added — effectively random init). All future retrains should fill in a specific seed integer. Note: even with the seed set, training isn't fully deterministic — variance is reduced from ±0.05 F1 to ±0.02-ish, but some torch ops aren't strictly seeded.

### Latest comparison: Apr-08 vs Apr-26 (via `eval_compare.py` 2026-04-30)

Apr-25 ONNX is unrecoverable (overwritten before commit), so the head-to-head is Apr-08 vs Apr-26. Apr-25 numbers shown for context from the per-round table.

**seed-42 test split (557 clips, 209 positive):**

| Model | P | R | F1 | pos_mean | neg_mean |
|-------|---|---|----|----------|----------|
| Apr-08 | 0.652 | 0.880 | 0.749 | 0.883 | 0.315 |
| Apr-25 (lost) | 0.952 | 0.670 | 0.787 | 0.661 | 0.023 |
| **Apr-26** | **0.870** | **0.804** | **0.836** | 0.804 | 0.124 |

**recent-only (1279 clips Apr 9+, 78 positive):**

| Model | P | R | F1 |
|-------|---|---|----|
| Apr-08 | 0.063 | 0.346 | 0.107 |
| Apr-25 (lost) | 0.154 | 0.077 | 0.103 |
| **Apr-26** | **0.168** | **0.269** | **0.207** |

**Apr-25-pull 307 clean labels (23 positive):**

| Model | P | R | F1 |
|-------|---|---|----|
| Apr-08 | 0.109 | 0.304 | 0.161 |
| Apr-25 (lost) | 0.000 | 0.000 | 0.000 |
| **Apr-26** | **0.167** | **0.217** | **0.189** |

Headline: Apr-26 is the best model in every view. Recent-only F1 doubled vs Apr-25 (0.103 → 0.207) despite only 8 labels actually changing — likely random-init variance in the 56K-param CNN (training script doesn't seed `torch.manual_seed`). Recent-only F1 is still bad in absolute terms (0.207), so generalization to new acoustic conditions remains the next thing to chip at.

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
