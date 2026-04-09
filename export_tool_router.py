#!/usr/bin/env python3
"""
Export the trained tool router model to ONNX format and quantize to INT8.

Usage:
    cd whizvoice && python export_tool_router.py
    cd whizvoice && python export_tool_router.py --model-dir training_data/model
    cd whizvoice && python export_tool_router.py --skip-quantize  # ONNX only, no INT8
"""

import argparse
import json
import logging
import os
import sys
from typing import Dict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = "training_data/model"


def export_onnx(args):
    import torch
    import torch.nn as nn
    from transformers import DistilBertModel, DistilBertTokenizerFast

    # Import the model class from training script
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from train_tool_router import ToolRouterModel, PARAM_HEADS

    model_dir = args.model_dir

    # Load label map
    with open(os.path.join(model_dir, "label_map.json")) as f:
        label_map = json.load(f)
    num_classes = len(label_map)
    logger.info(f"Loaded label map with {num_classes} classes")

    # Load model
    device = torch.device("cpu")  # Export on CPU
    model = ToolRouterModel(num_classes).to(device)
    checkpoint = torch.load(os.path.join(model_dir, "best_model.pt"),
                            map_location=device, weights_only=True)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    logger.info(f"Loaded model from epoch {checkpoint['epoch']} (val_acc={checkpoint['val_acc']:.4f})")

    # Create dummy input
    tokenizer = DistilBertTokenizerFast.from_pretrained(os.path.join(model_dir, "tokenizer"))
    dummy_text = "set a timer for 5 minutes"
    dummy_encoding = tokenizer(dummy_text, max_length=64, padding="max_length",
                               truncation=True, return_tensors="pt")
    dummy_input_ids = dummy_encoding["input_ids"]
    dummy_attention_mask = dummy_encoding["attention_mask"]

    # We need a wrapper that returns a flat tuple of outputs for ONNX export
    # (ONNX doesn't support dict outputs directly)

    # Collect output names
    output_names = ["route_logits"]
    param_head_keys = sorted(model.param_heads.keys())
    for key in param_head_keys:
        output_names.append(f"param_{key}")

    class OnnxWrapper(nn.Module):
        def __init__(self, model, param_head_keys):
            super().__init__()
            self.model = model
            self.param_head_keys = param_head_keys

        def forward(self, input_ids, attention_mask):
            route_logits, param_outputs = self.model(input_ids, attention_mask)
            outputs = [route_logits]
            for key in self.param_head_keys:
                outputs.append(param_outputs[key])
            return tuple(outputs)

    wrapper = OnnxWrapper(model, param_head_keys)
    wrapper.eval()

    # Export to ONNX
    onnx_path = os.path.join(model_dir, "tool_router.onnx")
    logger.info(f"Exporting to ONNX: {onnx_path}")

    torch.onnx.export(
        wrapper,
        (dummy_input_ids, dummy_attention_mask),
        onnx_path,
        input_names=["input_ids", "attention_mask"],
        output_names=output_names,
        dynamic_axes={
            "input_ids": {0: "batch_size"},
            "attention_mask": {0: "batch_size"},
            **{name: {0: "batch_size"} for name in output_names},
        },
        opset_version=14,
        do_constant_folding=True,
    )

    onnx_size = os.path.getsize(onnx_path) / (1024 * 1024)
    logger.info(f"ONNX model size: {onnx_size:.1f} MB")

    # Save output name mapping
    output_map = {
        "route_logits": 0,
        "param_heads": {key: i + 1 for i, key in enumerate(param_head_keys)},
    }
    with open(os.path.join(model_dir, "onnx_output_map.json"), "w") as f:
        json.dump(output_map, f, indent=2)
    logger.info("Saved output name mapping to onnx_output_map.json")

    # Quantize to INT8
    if not args.skip_quantize:
        try:
            from onnxruntime.quantization import quantize_dynamic, QuantType
            quantized_path = os.path.join(model_dir, "tool_router_int8.onnx")
            logger.info(f"Quantizing to INT8: {quantized_path}")
            quantize_dynamic(
                onnx_path,
                quantized_path,
                weight_type=QuantType.QInt8,
            )
            quant_size = os.path.getsize(quantized_path) / (1024 * 1024)
            logger.info(f"Quantized model size: {quant_size:.1f} MB "
                        f"({100*(1-quant_size/onnx_size):.0f}% reduction)")
        except ImportError:
            logger.warning("onnxruntime not installed, skipping quantization. "
                           "Install with: pip install onnxruntime")

    # Verify with ONNX Runtime
    try:
        import onnxruntime as ort
        target_path = (os.path.join(model_dir, "tool_router_int8.onnx")
                       if not args.skip_quantize and os.path.exists(os.path.join(model_dir, "tool_router_int8.onnx"))
                       else onnx_path)
        session = ort.InferenceSession(target_path)
        result = session.run(None, {
            "input_ids": dummy_input_ids.numpy(),
            "attention_mask": dummy_attention_mask.numpy(),
        })
        import numpy as np
        route_probs = np.exp(result[0]) / np.exp(result[0]).sum(axis=-1, keepdims=True)
        pred_idx = route_probs.argmax(axis=-1)[0]
        id_to_label = {v: k for k, v in label_map.items()}
        pred_label = id_to_label[pred_idx]
        pred_conf = route_probs[0, pred_idx]

        logger.info(f"\nVerification with '{dummy_text}':")
        logger.info(f"  Predicted: {pred_label} (confidence: {pred_conf:.4f})")
        logger.info(f"  Number of outputs: {len(result)}")
        logger.info(f"\nExport complete!")
    except ImportError:
        logger.info("onnxruntime not installed, skipping verification")
        logger.info("\nExport complete (ONNX only)!")


def main():
    parser = argparse.ArgumentParser(description="Export tool router to ONNX")
    parser.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    parser.add_argument("--skip-quantize", action="store_true",
                        help="Skip INT8 quantization")
    args = parser.parse_args()

    export_onnx(args)


if __name__ == "__main__":
    main()
