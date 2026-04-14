#!/usr/bin/env python3
"""
Train a DistilBERT-based tool router model for on-device tool selection.

Architecture:
  DistilBERT encoder -> [CLS] representation (768-dim)
    -> Route classifier: N-class softmax (all tools + NEEDS_LLM)
    -> Per-tool parameter heads (only evaluated for the predicted tool)

Usage:
    cd whizvoice && python train_tool_router.py
    cd whizvoice && python train_tool_router.py --input training_data/augmented_training_data.jsonl
    cd whizvoice && python train_tool_router.py --epochs 15 --batch-size 32 --lr 2e-5
"""

import argparse
import json
import logging
import os
import re
import sys
from collections import Counter
from typing import List, Dict, Optional, Tuple

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_INPUT = "training_data/augmented_training_data.jsonl"
DEFAULT_OUTPUT_DIR = "training_data/model"


# ── Data Parsing ─────────────────────────────────────────────────────────────

def parse_output(output_str: str) -> Tuple[str, dict]:
    """Parse training output string into (tool_name_or_NEEDS_LLM, params_dict).

    Returns:
        ("NEEDS_LLM", {}) for NEEDS_LLM examples
        ("agent_set_timer", {"seconds": 300}) for tool examples
    """
    if output_str == "NEEDS_LLM":
        return "NEEDS_LLM", {}

    if output_str.startswith("TOOL "):
        parts = output_str.split(" ", 2)
        tool_name = parts[1]
        params = json.loads(parts[2]) if len(parts) > 2 else {}
        return tool_name, params

    raise ValueError(f"Unknown output format: {output_str}")


def load_data(path: str) -> List[Dict]:
    """Load JSONL training data."""
    examples = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            tool_name, params = parse_output(raw["output"])
            examples.append({
                "input": raw["input"],
                "tool_name": tool_name,
                "params": params,
            })
    return examples


def build_label_map(examples: List[Dict]) -> Dict[str, int]:
    """Build tool_name -> label_id mapping. NEEDS_LLM gets the last id."""
    tool_names = sorted(set(e["tool_name"] for e in examples if e["tool_name"] != "NEEDS_LLM"))
    label_map = {name: i for i, name in enumerate(tool_names)}
    label_map["NEEDS_LLM"] = len(tool_names)
    return label_map


# ── Parameter Head Definitions ───────────────────────────────────────────────

# Each tool that has parameters gets a head definition.
# Types: "int_regression" (output a single number), "int_class" (classify into N bins),
#        "bool" (binary), "enum" (N-way classification), "span" (extract from input text)
PARAM_HEADS = {
    "agent_set_timer": [
        {"name": "seconds", "type": "int_regression"},
    ],
    "agent_set_alarm": [
        {"name": "hour", "type": "int_class", "num_classes": 24},
        {"name": "minute", "type": "int_class", "num_classes": 60},
    ],
    "agent_delete_alarm": [
        {"name": "hour", "type": "int_class", "num_classes": 24},
        {"name": "minute", "type": "int_class", "num_classes": 60},
    ],
    "agent_set_volume": [
        {"name": "volume_level", "type": "int_class", "num_classes": 101},  # 0-100
    ],
    "agent_fitbit_add_quick_calories": [
        {"name": "calories", "type": "int_regression"},
    ],
    "get_weather": [
        {"name": "days_ahead", "type": "int_class", "num_classes": 7},  # 0-6
    ],
    "agent_toggle_flashlight": [
        {"name": "turn_on", "type": "bool"},
    ],
    "agent_set_tts_enabled": [
        {"name": "enabled", "type": "bool"},
    ],
    "agent_app_control": [
        {"name": "action", "type": "enum", "values": ["launch", "close"]},
        {"name": "app_name", "type": "span"},
    ],
    "agent_launch_app": [
        {"name": "app_name", "type": "span"},
    ],
    "agent_select_chat": [
        {"name": "app", "type": "enum", "values": ["whatsapp", "sms"]},
        {"name": "contact_name", "type": "span"},
    ],
    "agent_whatsapp_select_chat": [
        {"name": "chat_name", "type": "span"},
    ],
    "agent_sms_select_chat": [
        {"name": "contact_name", "type": "span"},
    ],
    "agent_draft_message": [
        {"name": "app", "type": "enum", "values": ["whatsapp", "sms"]},
        {"name": "message", "type": "span"},
        {"name": "contact_name", "type": "span"},
    ],
    "agent_whatsapp_draft_message": [
        {"name": "message", "type": "span"},
    ],
    "agent_sms_draft_message": [
        {"name": "message", "type": "span"},
    ],
    "agent_send_message": [
        {"name": "app", "type": "enum", "values": ["whatsapp", "sms"]},
        {"name": "message", "type": "span"},
    ],
    "agent_whatsapp_send_message": [
        {"name": "message", "type": "span"},
    ],
    "agent_sms_send_message": [
        {"name": "message", "type": "span"},
    ],
    "agent_youtube_music": [
        {"name": "action", "type": "enum", "values": ["play", "queue"]},
        {"name": "query", "type": "span"},
    ],
    "agent_play_youtube_music": [
        {"name": "query", "type": "span"},
    ],
    "agent_queue_youtube_music": [
        {"name": "query", "type": "span"},
    ],
    "agent_search_google_maps_location": [
        {"name": "address_keyword", "type": "span"},
    ],
    "agent_search_google_maps_phrase": [
        {"name": "search_phrase", "type": "span"},
    ],
    "agent_get_google_maps_directions": [
        {"name": "mode", "type": "enum", "values": ["drive", "walk", "bike", "transit"]},
    ],
    "agent_dial_phone_number": [
        {"name": "phone_number", "type": "span"},
    ],
    "agent_lookup_phone_contacts": [
        {"name": "name", "type": "span"},
    ],
    "get_contact_preference": [
        {"name": "name", "type": "span"},
    ],
    "remove_contact_preference": [
        {"name": "name", "type": "span"},
    ],
    "get_new_asana_task_id": [
        {"name": "name", "type": "span"},
    ],
    "update_asana_task": [
        {"name": "task_gid", "type": "span"},
    ],
    "delete_asana_task": [
        {"name": "task_gid", "type": "span"},
    ],
    "get_info": [
        {"name": "type", "type": "enum", "values": ["app", "user_data"]},
    ],
    "manage_music_app_preference": [
        {"name": "action", "type": "enum", "values": ["get", "set"]},
    ],
    "manage_workspace_preference": [
        {"name": "action", "type": "enum", "values": ["get", "set"]},
    ],
    "manage_parent_task_preference": [
        {"name": "action", "type": "enum", "values": ["get", "set"]},
    ],
    "agent_calendar_event": [
        {"name": "action", "type": "enum", "values": ["draft", "save"]},
        {"name": "title", "type": "span"},
        {"name": "begin_time", "type": "span"},
    ],
    "agent_draft_calendar_event": [
        {"name": "title", "type": "span"},
        {"name": "begin_time", "type": "span"},
    ],
    "agent_close_other_app": [
        {"name": "app_name", "type": "span"},
    ],
    "add_contact_preference": [
        {"name": "real_name", "type": "span"},
        {"name": "preferred_app", "type": "enum", "values": ["whatsapp", "sms"]},
    ],
    "save_location": [
        {"name": "location_name", "type": "span"},
        {"name": "location_type", "type": "span"},
    ],
    "set_user_timezone": [
        {"name": "timezone", "type": "span"},
    ],
}


# ── PyTorch Model ────────────────────────────────────────────────────────────

import torch
import torch.nn as nn
from transformers import DistilBertModel


class ToolRouterModel(nn.Module):
    def __init__(self, num_route_classes, hidden_size=768, dropout=0.1):
        super().__init__()
        self.distilbert = DistilBertModel.from_pretrained("distilbert-base-uncased")
        self.dropout = nn.Dropout(dropout)

        # Route classifier
        self.route_classifier = nn.Linear(hidden_size, num_route_classes)

        # Parameter heads - built dynamically from PARAM_HEADS
        self.param_heads = nn.ModuleDict()
        for tool_name, heads in PARAM_HEADS.items():
            for head_def in heads:
                pname = head_def["name"]
                ptype = head_def["type"]
                key = f"{tool_name}__{pname}"  # ModuleDict doesn't allow dots

                if ptype == "int_regression":
                    self.param_heads[key] = nn.Linear(hidden_size, 1)
                elif ptype == "int_class":
                    self.param_heads[key] = nn.Linear(hidden_size, head_def["num_classes"])
                elif ptype == "bool":
                    self.param_heads[key] = nn.Linear(hidden_size, 1)
                elif ptype == "enum":
                    self.param_heads[key] = nn.Linear(hidden_size, len(head_def["values"]))
                elif ptype == "span":
                    # Start + end token prediction
                    self.param_heads[key] = nn.Linear(hidden_size, 2)

    def forward(self, input_ids, attention_mask):
        outputs = self.distilbert(input_ids=input_ids, attention_mask=attention_mask)
        # [CLS] token (first token)
        cls_output = outputs.last_hidden_state[:, 0, :]
        cls_output = self.dropout(cls_output)

        # Sequence output for span extraction
        sequence_output = self.dropout(outputs.last_hidden_state)

        route_logits = self.route_classifier(cls_output)

        # Compute all parameter head outputs
        param_outputs = {}
        for key, head in self.param_heads.items():
            tool_name, pname = key.split("__", 1)
            # Find the head definition
            head_def = None
            if tool_name in PARAM_HEADS:
                for hd in PARAM_HEADS[tool_name]:
                    if hd["name"] == pname:
                        head_def = hd
                        break

            if head_def and head_def["type"] == "span":
                # Span: project each token to 2 logits (start, end)
                span_logits = head(sequence_output)  # [batch, seq_len, 2]
                param_outputs[key] = span_logits
            else:
                param_outputs[key] = head(cls_output)

        return route_logits, param_outputs


def train(args):
    from torch.utils.data import Dataset, DataLoader
    from transformers import DistilBertTokenizerFast
    from sklearn.model_selection import train_test_split

    device = torch.device("mps" if torch.backends.mps.is_available()
                          else "cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Using device: {device}")

    # Load data
    examples = load_data(args.input)
    logger.info(f"Loaded {len(examples)} examples")

    label_map = build_label_map(examples)
    num_classes = len(label_map)
    id_to_label = {v: k for k, v in label_map.items()}
    logger.info(f"Number of route classes: {num_classes} ({num_classes - 1} tools + NEEDS_LLM)")

    # Save label map for inference
    os.makedirs(args.output_dir, exist_ok=True)
    with open(os.path.join(args.output_dir, "label_map.json"), "w") as f:
        json.dump(label_map, f, indent=2)
    with open(os.path.join(args.output_dir, "param_heads.json"), "w") as f:
        json.dump(PARAM_HEADS, f, indent=2)

    # Tokenizer
    tokenizer = DistilBertTokenizerFast.from_pretrained("distilbert-base-uncased")
    tokenizer.save_pretrained(os.path.join(args.output_dir, "tokenizer"))

    # ── Dataset ──────────────────────────────────────────────────────────

    class ToolRouterDataset(Dataset):
        def __init__(self, examples, tokenizer, label_map, max_length=64):
            self.examples = examples
            self.tokenizer = tokenizer
            self.label_map = label_map
            self.max_length = max_length

        def __len__(self):
            return len(self.examples)

        def __getitem__(self, idx):
            ex = self.examples[idx]
            encoding = self.tokenizer(
                ex["input"],
                max_length=self.max_length,
                padding="max_length",
                truncation=True,
                return_tensors="pt",
            )

            item = {
                "input_ids": encoding["input_ids"].squeeze(0),
                "attention_mask": encoding["attention_mask"].squeeze(0),
                "route_label": torch.tensor(self.label_map[ex["tool_name"]], dtype=torch.long),
            }

            # Parameter targets
            tool_name = ex["tool_name"]
            params = ex["params"]

            if tool_name in PARAM_HEADS:
                for head_def in PARAM_HEADS[tool_name]:
                    pname = head_def["name"]
                    ptype = head_def["type"]
                    pval = params.get(pname)

                    if ptype == "int_regression" and pval is not None:
                        item[f"param_{tool_name}_{pname}"] = torch.tensor(float(pval), dtype=torch.float)
                    elif ptype == "int_class" and pval is not None:
                        num_c = head_def["num_classes"]
                        val = int(pval)
                        val = max(0, min(val, num_c - 1))
                        item[f"param_{tool_name}_{pname}"] = torch.tensor(val, dtype=torch.long)
                    elif ptype == "bool" and pval is not None:
                        item[f"param_{tool_name}_{pname}"] = torch.tensor(float(bool(pval)), dtype=torch.float)
                    elif ptype == "enum" and pval is not None:
                        values = head_def["values"]
                        if pval in values:
                            item[f"param_{tool_name}_{pname}"] = torch.tensor(values.index(pval), dtype=torch.long)

            return item

    # ── Model ────────────────────────────────────────────────────────────

    # ── Training Loop ────────────────────────────────────────────────────

    # Split data - handle rare classes that can't be stratified
    tool_counts = Counter(e["tool_name"] for e in examples)
    # For classes with <2 examples, duplicate them so stratification works
    augmented_examples = list(examples)
    for tool_name, count in tool_counts.items():
        if count < 2:
            extras = [e for e in examples if e["tool_name"] == tool_name]
            augmented_examples.extend(extras)  # duplicate rare examples

    train_examples, val_examples = train_test_split(
        augmented_examples, test_size=0.1, random_state=42,
        stratify=[e["tool_name"] for e in augmented_examples]
    )
    logger.info(f"Train: {len(train_examples)}, Val: {len(val_examples)}")

    train_dataset = ToolRouterDataset(train_examples, tokenizer, label_map, max_length=args.max_length)
    val_dataset = ToolRouterDataset(val_examples, tokenizer, label_map, max_length=args.max_length)

    def custom_collate(batch):
        """Collate function that handles variable keys across examples.
        Only includes param keys that are present in ALL examples of the batch,
        or pads missing param values with NaN/(-1) for partial batches.
        """
        # Always-present keys
        result = {
            "input_ids": torch.stack([b["input_ids"] for b in batch]),
            "attention_mask": torch.stack([b["attention_mask"] for b in batch]),
            "route_label": torch.stack([b["route_label"] for b in batch]),
        }

        # Collect all param keys present in any example
        all_param_keys = set()
        for b in batch:
            for k in b:
                if k.startswith("param_"):
                    all_param_keys.add(k)

        # For each param key, create a padded tensor
        for key in all_param_keys:
            values = []
            for b in batch:
                if key in b:
                    values.append(b[key])
                else:
                    # Use NaN for float, -1 for long as "missing" sentinel
                    sample = next(b2[key] for b2 in batch if key in b2)
                    if sample.dtype == torch.float:
                        values.append(torch.tensor(float("nan"), dtype=torch.float))
                    else:
                        values.append(torch.tensor(-1, dtype=torch.long))
            result[key] = torch.stack(values)

        return result

    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True,
                              num_workers=0, collate_fn=custom_collate)
    val_loader = DataLoader(val_dataset, batch_size=args.batch_size, shuffle=False,
                            num_workers=0, collate_fn=custom_collate)

    # Model
    model = ToolRouterModel(num_classes).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)

    # Learning rate scheduler with warmup
    total_steps = len(train_loader) * args.epochs
    warmup_steps = int(0.1 * total_steps)

    def lr_lambda(step):
        if step < warmup_steps:
            return step / max(warmup_steps, 1)
        return max(0.0, 1.0 - (step - warmup_steps) / max(total_steps - warmup_steps, 1))

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)

    # Loss functions
    route_criterion = nn.CrossEntropyLoss()
    regression_criterion = nn.MSELoss()
    bool_criterion = nn.BCEWithLogitsLoss()
    class_criterion = nn.CrossEntropyLoss()

    best_val_acc = 0.0
    patience_counter = 0

    for epoch in range(args.epochs):
        # ── Train ────────────────────────────────────────────────────
        model.train()
        total_loss = 0.0
        route_correct = 0
        route_total = 0

        for batch_idx, batch in enumerate(train_loader):
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            route_labels = batch["route_label"].to(device)

            route_logits, param_outputs = model(input_ids, attention_mask)

            # Route loss
            loss = route_criterion(route_logits, route_labels)

            # Parameter losses (only for examples where we have ground truth)
            param_loss = torch.tensor(0.0, device=device)
            param_count = 0

            for key, output in param_outputs.items():
                tool_name, pname = key.split("__", 1)
                param_key = f"param_{tool_name}_{pname}"

                if param_key not in batch:
                    continue

                target = batch[param_key].to(device)
                # Only compute loss for examples that have this param
                mask = ~torch.isnan(target) if target.dtype == torch.float else (target >= 0)
                if mask.sum() == 0:
                    continue

                # Find head definition
                head_def = None
                if tool_name in PARAM_HEADS:
                    for hd in PARAM_HEADS[tool_name]:
                        if hd["name"] == pname:
                            head_def = hd
                            break

                if head_def is None:
                    continue

                ptype = head_def["type"]
                masked_output = output[mask]
                masked_target = target[mask]

                if ptype == "int_regression":
                    # Normalize large values for stable training
                    pl = regression_criterion(masked_output.squeeze(-1), masked_target)
                    param_loss = param_loss + pl
                    param_count += 1
                elif ptype == "int_class":
                    pl = class_criterion(masked_output, masked_target)
                    param_loss = param_loss + pl
                    param_count += 1
                elif ptype == "bool":
                    pl = bool_criterion(masked_output.squeeze(-1), masked_target)
                    param_loss = param_loss + pl
                    param_count += 1
                elif ptype == "enum":
                    pl = class_criterion(masked_output, masked_target)
                    param_loss = param_loss + pl
                    param_count += 1

            if param_count > 0:
                loss = loss + 0.5 * (param_loss / param_count)

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()

            total_loss += loss.item()
            preds = route_logits.argmax(dim=-1)
            route_correct += (preds == route_labels).sum().item()
            route_total += len(route_labels)

        train_acc = route_correct / route_total
        avg_train_loss = total_loss / len(train_loader)

        # ── Validate ─────────────────────────────────────────────────
        model.eval()
        val_correct = 0
        val_total = 0
        val_loss = 0.0

        with torch.no_grad():
            for batch in val_loader:
                input_ids = batch["input_ids"].to(device)
                attention_mask = batch["attention_mask"].to(device)
                route_labels = batch["route_label"].to(device)

                route_logits, _ = model(input_ids, attention_mask)
                loss = route_criterion(route_logits, route_labels)
                val_loss += loss.item()

                preds = route_logits.argmax(dim=-1)
                val_correct += (preds == route_labels).sum().item()
                val_total += len(route_labels)

        val_acc = val_correct / val_total
        avg_val_loss = val_loss / len(val_loader)

        logger.info(f"Epoch {epoch+1}/{args.epochs}: "
                    f"train_loss={avg_train_loss:.4f} train_acc={train_acc:.4f} "
                    f"val_loss={avg_val_loss:.4f} val_acc={val_acc:.4f}")

        # Early stopping / best model
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            patience_counter = 0
            # Save best model
            torch.save({
                "model_state_dict": model.state_dict(),
                "label_map": label_map,
                "num_classes": num_classes,
                "val_acc": val_acc,
                "epoch": epoch + 1,
            }, os.path.join(args.output_dir, "best_model.pt"))
            logger.info(f"  -> New best model saved (val_acc={val_acc:.4f})")
        else:
            patience_counter += 1
            if patience_counter >= args.patience:
                logger.info(f"Early stopping at epoch {epoch+1} (patience={args.patience})")
                break

    logger.info(f"\nTraining complete. Best val accuracy: {best_val_acc:.4f}")
    logger.info(f"Model saved to: {args.output_dir}")

    # ── Final Evaluation ─────────────────────────────────────────────────

    # Load best model
    checkpoint = torch.load(os.path.join(args.output_dir, "best_model.pt"),
                            map_location=device, weights_only=True)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()

    # Detailed per-class accuracy
    class_correct = Counter()
    class_total = Counter()
    confusion_examples = []  # Store some misclassified examples for analysis

    with torch.no_grad():
        for batch in val_loader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            route_labels = batch["route_label"].to(device)

            route_logits, _ = model(input_ids, attention_mask)
            preds = route_logits.argmax(dim=-1)
            confidences = torch.softmax(route_logits, dim=-1).max(dim=-1).values

            for i in range(len(route_labels)):
                true_label = id_to_label[route_labels[i].item()]
                pred_label = id_to_label[preds[i].item()]
                class_total[true_label] += 1
                if true_label == pred_label:
                    class_correct[true_label] += 1
                elif len(confusion_examples) < 20:
                    # Decode input for analysis
                    tokens = tokenizer.decode(input_ids[i], skip_special_tokens=True)
                    confusion_examples.append({
                        "input": tokens[:80],
                        "true": true_label,
                        "pred": pred_label,
                        "confidence": f"{confidences[i].item():.3f}",
                    })

    print(f"\n{'=' * 70}")
    print(f"Per-class accuracy on validation set:")
    print(f"{'=' * 70}")
    for label in sorted(class_total.keys()):
        acc = class_correct[label] / class_total[label] if class_total[label] > 0 else 0
        print(f"  {label:45s}: {class_correct[label]:4d}/{class_total[label]:4d} = {acc:.3f}")

    if confusion_examples:
        print(f"\nMisclassified examples (sample):")
        for ex in confusion_examples[:10]:
            print(f"  \"{ex['input']}\"")
            print(f"    true={ex['true']}, pred={ex['pred']}, conf={ex['confidence']}")

    print(f"\nOverall val accuracy: {best_val_acc:.4f}")
    print(f"{'=' * 70}")


def main():
    parser = argparse.ArgumentParser(description="Train tool router model")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--epochs", type=int, default=15)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--max-length", type=int, default=64)
    parser.add_argument("--patience", type=int, default=3)
    args = parser.parse_args()

    train(args)


if __name__ == "__main__":
    main()
