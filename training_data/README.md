# Tool Router Training Data Pipeline

This directory contains data and models for the on-device tool router, which predicts
which tool to call (and with what parameters) from a user's voice utterance, without
going to the cloud LLM.

The `.jsonl` data files and `model/` directory are gitignored since they're large and
regenerated from the scripts. Only the scripts and this README are checked in.

## Quick Start

All scripts live in `whizvoice/` (one directory up from here). Run from there:

```bash
cd whizvoice

# Step 1: Extract real conversation data from Supabase
./venv/bin/python extract_training_data.py

# Step 2: Augment with synthetic examples (requires ANTHROPIC_API_KEY)
source ../whizvoiceapp/export_anthropic_key.sh
./venv/bin/python augment_training_data.py

# Step 3: Train the model (requires torch, transformers, scikit-learn)
./venv/bin/python train_tool_router.py

# Step 4: Export to ONNX (requires onnxruntime)
./venv/bin/python export_tool_router.py
```

Install dependencies if needed:
```bash
./venv/bin/pip install transformers scikit-learn onnxruntime
```
(`torch` and `supabase` should already be in the venv.)

## Retraining With More Data

As the app accumulates more conversation history, retraining is straightforward:

```bash
cd whizvoice

# Re-extract (picks up all new conversations since last time)
./venv/bin/python extract_training_data.py

# Re-augment (normalizes legacy tool names, fills in underrepresented tools)
source ../whizvoiceapp/export_anthropic_key.sh
./venv/bin/python augment_training_data.py

# Retrain (overwrites previous model in training_data/model/)
./venv/bin/python train_tool_router.py

# Re-export ONNX
./venv/bin/python export_tool_router.py
```

Each script prints stats so you can see how the data and accuracy have changed.

### Tips for improving accuracy

- **More real data helps the most.** Just using the app generates training examples.
- **Clean noisy examples.** Many mid-conversation utterances like "okay", "please", "yes"
  are labeled with whatever tool the LLM happened to call next, but they're actually
  context-dependent. Filtering these out or relabeling them as NEEDS_LLM would help.
- **Increase augmentation.** Use `--target-per-tool 100` (or higher) to generate more
  synthetic examples for underrepresented tools.
- **Add templates.** For tools with few real examples, add handwritten templates to
  `TOOL_SCHEMAS` in `augment_training_data.py`.

## Training Data Format

Each line in the JSONL files is one training example:

```json
{"input": "set a timer for 5 minutes", "output": "TOOL agent_set_timer {\"seconds\": 300}"}
{"input": "how are you doing today", "output": "NEEDS_LLM"}
```

- `input`: User utterance text (transcribed from voice)
- `output`: Either `TOOL <tool_name> <params_json>` or `NEEDS_LLM`

## Generated Files (gitignored)

| File | Description |
|------|-------------|
| `raw_training_data.jsonl` | Extracted from Supabase conversation history |
| `augmented_training_data.jsonl` | After normalization, templates, and Claude-generated examples |
| `model/best_model.pt` | Best PyTorch checkpoint |
| `model/label_map.json` | Tool name -> class ID mapping |
| `model/param_heads.json` | Parameter head definitions per tool |
| `model/tokenizer/` | Saved DistilBERT tokenizer |
| `model/tool_router.onnx` | Exported ONNX model (FP32, ~255MB) |
| `model/tool_router_int8.onnx` | Quantized ONNX model (INT8, ~64MB) |
| `model/onnx_output_map.json` | ONNX output tensor name mapping |

## Scripts Reference

### extract_training_data.py

Connects to Supabase and pulls all conversation messages. For each user text message,
finds the next assistant response. If the assistant called a tool, the example becomes
`TOOL <name> <params>`. If not, it becomes `NEEDS_LLM`.

```bash
# Print stats without writing file (good for checking data health)
./venv/bin/python extract_training_data.py --stats

# Write to custom path
./venv/bin/python extract_training_data.py --output training_data/my_data.jsonl
```

**Requires:** Supabase credentials in `constants.py` (via `supabase_client.py`).

As of 2026-04-08: extracted 17,495 examples from 47,199 messages across 2,612 conversations.

### augment_training_data.py

Takes the raw extracted data and:
1. Normalizes legacy tool names (e.g., `launch_app` -> `agent_launch_app`)
2. Adds handwritten template examples from `TOOL_SCHEMAS` dict
3. Calls Claude Haiku API to generate diverse paraphrases for underrepresented tools
4. Deduplicates

```bash
# Templates + normalization only (no API calls, fast)
./venv/bin/python augment_training_data.py --skip-generation

# More examples per tool
./venv/bin/python augment_training_data.py --target-per-tool 100
```

**Requires:** `ANTHROPIC_API_KEY` env var for Claude generation
(`source ../whizvoiceapp/export_anthropic_key.sh`).

As of 2026-04-08: produced 14,726 examples after augmentation and dedup.

### train_tool_router.py

Fine-tunes `distilbert-base-uncased` with a multi-task architecture:
- **Route classifier head:** softmax over all tools + NEEDS_LLM
- **Per-tool parameter heads:** regression, classification, boolean, enum, and
  span extraction (SQuAD-style start/end token pointers)

Uses early stopping with patience=3 on validation accuracy.

```bash
# Default: 15 epochs, batch size 32, lr 2e-5
./venv/bin/python train_tool_router.py

# Custom hyperparameters
./venv/bin/python train_tool_router.py --epochs 20 --batch-size 16 --lr 3e-5
```

After training, prints per-class accuracy and sample misclassifications.

**Requires:** `torch`, `transformers`, `scikit-learn`.

As of 2026-04-08: 81.7% val accuracy (early stopped at epoch 10). Best on simple tools
(flashlight, alarms, close app: 80-100%), weaker on context-dependent tools.

### export_tool_router.py

Exports the best PyTorch checkpoint to ONNX and quantizes to INT8.

```bash
# Full export + INT8 quantization
./venv/bin/python export_tool_router.py

# ONNX only, skip quantization
./venv/bin/python export_tool_router.py --skip-quantize
```

Runs a verification inference at the end to confirm the exported model works.

**Requires:** `torch`, `transformers`, `onnxruntime`.

## Adding a New Tool

1. **Real data:** If the tool is in production, just re-run `extract_training_data.py`.

2. **Augmentation templates:** Add to `TOOL_SCHEMAS` in `augment_training_data.py`:
   - Zero-param tools: list of utterance strings
   - Parameterized tools: list of `(utterance, params_dict)` tuples

3. **Parameter head:** Add to `PARAM_HEADS` in `train_tool_router.py`:
   - Types: `int_regression`, `int_class`, `bool`, `enum`, `span`

4. **Re-run the full pipeline** (see Quick Start above).

5. **Deploy:** Copy `model/tool_router_int8.onnx` and `model/label_map.json` to
   `whizvoiceapp/app/src/main/assets/`.

## Model Architecture

```
User text (max 64 tokens)
  -> DistilBERT encoder (distilbert-base-uncased, 66M params)
  -> [CLS] token representation (768-dim)
  -> Route classifier: softmax over all tools + NEEDS_LLM
  -> Per-tool parameter heads (only evaluated for predicted tool):
     - Boolean params: sigmoid
     - Numeric classification: softmax over value range
     - Numeric regression: linear output
     - Enum params: softmax over enum values
     - Span params: start/end token pointers (SQuAD-style)
```

Confidence threshold: If `max(softmax) < 0.85`, defer to LLM regardless of prediction.
This threshold should be tuned on real traffic — we'd rather send to the LLM unnecessarily
than make a wrong tool call.
