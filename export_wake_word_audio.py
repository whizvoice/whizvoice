#!/usr/bin/env python3
"""
Export wake word audio clips from Supabase Storage to local disk and purge from cloud.

Downloads all WAV files and metadata, saves locally, then deletes from Supabase
to free up storage space.

Usage:
    python export_wake_word_audio.py
    python export_wake_word_audio.py --output ~/my_training_data
"""

import argparse
import csv
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from supabase_client import supabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BUCKET_NAME = "wake-word-audio"
TABLE_NAME = "wake_word_audio_clips"
DEFAULT_OUTPUT_DIR = os.path.expanduser("~/wake_word_training_data")

CSV_FIELDS = [
    "id", "created_at", "user_id", "phrase", "confidence", "accepted",
    "detection_timestamp", "raw_vosk_json", "storage_path", "file_size_bytes",
    "classifier_score", "local_filename"
]


def export_clips(output_dir: str):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Query all rows
    logger.info("Querying wake_word_audio_clips table...")
    result = supabase.table(TABLE_NAME).select("*").order("created_at", desc=False).execute()

    if not result.data:
        logger.info("No audio clips found. Nothing to export.")
        return

    clips = result.data
    logger.info(f"Found {len(clips)} audio clips to export.")

    # Prepare CSV — append mode so multiple runs don't overwrite
    csv_path = output_path / "metadata.csv"
    csv_exists = csv_path.exists()
    csv_file = open(csv_path, "a", newline="")
    writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
    if not csv_exists:
        writer.writeheader()

    downloaded = 0
    skipped = 0
    failed = 0
    total_bytes = 0

    for clip in clips:
        clip_id = clip["id"]
        storage_path = clip["storage_path"]
        detection_ts = clip.get("detection_timestamp", 0)

        # Organize by date
        try:
            dt = datetime.fromtimestamp(detection_ts / 1000, tz=timezone.utc)
            date_dir = dt.strftime("%Y-%m-%d")
        except (ValueError, OSError):
            date_dir = "unknown"

        day_path = output_path / date_dir
        day_path.mkdir(parents=True, exist_ok=True)

        # Derive local filename from storage path
        filename = os.path.basename(storage_path)
        local_file = day_path / filename

        # Skip if already downloaded (for partial run recovery)
        if local_file.exists():
            logger.info(f"  [{clip_id}] Already exists locally, skipping download: {local_file}")
            skipped += 1
            # Still write to CSV and delete from cloud
        else:
            # Download from Supabase Storage
            try:
                file_data = supabase.storage.from_(BUCKET_NAME).download(storage_path)
                with open(local_file, "wb") as f:
                    f.write(file_data)
                logger.info(f"  [{clip_id}] Downloaded: {local_file} ({len(file_data)} bytes)")
                downloaded += 1
                total_bytes += len(file_data)
            except Exception as e:
                logger.error(f"  [{clip_id}] Failed to download {storage_path}: {e}")
                failed += 1
                continue  # Don't delete from cloud if download failed

        # Verify local file exists before deleting from cloud
        if not local_file.exists():
            logger.error(f"  [{clip_id}] Local file missing after download, skipping cloud delete")
            failed += 1
            continue

        # Write metadata to CSV
        row = {field: clip.get(field, "") for field in CSV_FIELDS if field != "local_filename"}
        row["local_filename"] = str(local_file.relative_to(output_path))
        writer.writerow(row)

        # Delete from Supabase Storage
        try:
            supabase.storage.from_(BUCKET_NAME).remove([storage_path])
        except Exception as e:
            logger.warning(f"  [{clip_id}] Failed to delete from storage (will retry next run): {e}")

        # Delete database row
        try:
            supabase.table(TABLE_NAME).delete().eq("id", clip_id).execute()
        except Exception as e:
            logger.warning(f"  [{clip_id}] Failed to delete DB row (will retry next run): {e}")

    csv_file.close()

    # Summary
    logger.info("")
    logger.info("=" * 50)
    logger.info(f"Export complete!")
    logger.info(f"  Downloaded: {downloaded} clips ({total_bytes / 1024:.1f} KB)")
    logger.info(f"  Skipped (already local): {skipped}")
    logger.info(f"  Failed: {failed}")
    logger.info(f"  Output directory: {output_path}")
    logger.info(f"  Metadata CSV: {csv_path}")
    logger.info("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="Export wake word audio clips from Supabase")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR, help="Output directory (default: ~/wake_word_training_data)")
    args = parser.parse_args()

    export_clips(args.output)


if __name__ == "__main__":
    main()
