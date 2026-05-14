"""Export reviewed cargo handwriting OCR samples into a simple line dataset.

The review flow stores accepted scan rows under:
  frontend/.runtime/cargo_ocr_samples/<sample_id>/

This script copies each reviewed line crop into an output image folder and
writes a labels.txt file with:
  relative/image/path<TAB>ground truth text
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SAMPLE_ROOT = REPO_ROOT / "frontend" / ".runtime" / "cargo_ocr_samples"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "datasets" / "cargo_ocr_vietocr"


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value)


def iter_reviewed_lines(sample_root: Path, include_legacy: bool, changed_only: bool, min_confidence: float):
    for sample_json in sorted(sample_root.glob("*/sample.json")):
        try:
            sample = json.loads(sample_json.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"skip unreadable sample: {sample_json} ({exc})")
            continue

        sample_dir = sample_json.parent
        source_lines = sample.get("ocr", {}).get("source_lines", [])
        for pos, line in enumerate(source_lines, start=1):
            crop_file = line.get("crop_file")
            if not crop_file:
                continue

            confidence = float(line.get("confidence") or 0.0)
            if confidence < min_confidence:
                continue

            has_reviewed_text = "reviewed_text" in line
            if changed_only and not line.get("reviewed_text_changed"):
                continue
            if has_reviewed_text:
                text = str(line.get("reviewed_text") or "").strip()
            elif include_legacy:
                text = str(line.get("text") or "").strip()
            else:
                continue

            if not text:
                continue

            image_path = sample_dir / crop_file
            if not image_path.exists():
                continue

            yield {
                "sample_id": sample_dir.name,
                "line_pos": pos,
                "image_path": image_path,
                "text": text,
                "confidence": confidence,
                "changed": bool(line.get("reviewed_text_changed")),
            }


def export_dataset(
    sample_root: Path,
    output_dir: Path,
    include_legacy: bool,
    changed_only: bool,
    min_confidence: float,
) -> dict:
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for item in iter_reviewed_lines(sample_root, include_legacy, changed_only, min_confidence):
        image_name = _safe_name(f"{item['sample_id']}_line_{item['line_pos']:02d}{item['image_path'].suffix.lower()}")
        dest = images_dir / image_name
        shutil.copy2(item["image_path"], dest)
        rel_path = dest.relative_to(output_dir).as_posix()
        rows.append((rel_path, item["text"], item))

    labels_path = output_dir / "labels.txt"
    labels_path.write_text(
        "".join(f"{rel_path}\t{text}\n" for rel_path, text, _ in rows),
        encoding="utf-8",
    )

    manifest = {
        "sample_root": str(sample_root),
        "output_dir": str(output_dir),
        "line_count": len(rows),
        "changed_line_count": sum(1 for _, _, item in rows if item["changed"]),
        "min_confidence": min_confidence,
        "include_legacy": include_legacy,
        "changed_only": changed_only,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Export reviewed cargo OCR line crops for VietOCR fine-tuning.")
    parser.add_argument("--sample-root", type=Path, default=DEFAULT_SAMPLE_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--include-legacy", action="store_true", help="Use OCR text for old samples without reviewed_text.")
    parser.add_argument("--changed-only", action="store_true", help="Export only lines whose reviewed text changed.")
    parser.add_argument("--min-confidence", type=float, default=0.0)
    args = parser.parse_args()

    if not args.sample_root.exists():
        raise SystemExit(f"sample root does not exist: {args.sample_root}")

    manifest = export_dataset(
        sample_root=args.sample_root,
        output_dir=args.output_dir,
        include_legacy=args.include_legacy,
        changed_only=args.changed_only,
        min_confidence=args.min_confidence,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
