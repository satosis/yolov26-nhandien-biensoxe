#!/usr/bin/env python3
"""
Script tải dataset public từ Roboflow + train YOLOv11 custom model.

Classes mục tiêu:
  0: person
  1: car
  2: truck
  3: motorcycle
  4: license_plate
  5: door_open
  6: door_closed

Sử dụng:
  1. pip install roboflow ultralytics
  2. python3 train_custom_model.py --download    # Tải datasets
  3. python3 train_custom_model.py --train        # Train model
  4. python3 train_custom_model.py --export       # Export model tốt nhất
"""

import os
import sys
import shutil
import glob
import yaml
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DATASETS_DIR = PROJECT_ROOT / "datasets"
MERGED_DIR = DATASETS_DIR / "merged_dataset"
MODELS_DIR = PROJECT_ROOT / "models"

# ── Cấu hình class mapping ──
TARGET_CLASSES = {
    0: "person",
    1: "car",
    2: "truck",
    3: "motorcycle",
    4: "license_plate",
    5: "door_open",
    6: "door_closed",
}

# ── Roboflow datasets to download ──
# Format: (workspace, project, version, class_mapping)
# class_mapping: {source_class_id: target_class_id}
ROBOFLOW_DATASETS = [
    {
        "name": "Vietnamese License Plate",
        "workspace": "mocban",
        "project": "vietnam-license-plate-hqqmq",
        "version": 2,
        "format": "yolov11",
        "class_map": {"license_plate": 4, "License Plate": 4, "plate": 4, "0": 4},
    },
    {
        "name": "Door Open/Close Detection",
        "workspace": "fyp-xnjra",
        "project": "open-close-door-detection",
        "version": 2,
        "format": "yolov11",
        "class_map": {
            "door0close": 6, "doorobjectsopen": 5, "open door": 5,
            "close": 6, "open": 5, "closed": 6, "0": 6, "1": 5
        },
    },
]


def download_datasets(api_key: str):
    """Download datasets from Roboflow."""
    try:
        from roboflow import Roboflow
    except ImportError:
        print("❌ Cần cài: pip install roboflow")
        sys.exit(1)

    os.makedirs(DATASETS_DIR, exist_ok=True)
    rf = Roboflow(api_key=api_key)

    for ds in ROBOFLOW_DATASETS:
        print(f"\n📥 Đang tải: {ds['name']}...")
        try:
            project = rf.workspace(ds["workspace"]).project(ds["project"])
            version = project.version(ds["version"])
            dataset = version.download(ds["format"], location=str(DATASETS_DIR / ds["name"].replace(" ", "_")))
            print(f"  ✅ Đã tải: {dataset.location}")
        except Exception as e:
            print(f"  ⚠️ Lỗi tải {ds['name']}: {e}")
            print(f"  → Thử tải thủ công tại: https://universe.roboflow.com/{ds['workspace']}/{ds['project']}")

    print("\n🎯 Download hoàn tất! Tiếp theo chạy: python3 train_custom_model.py --merge")


def download_coco_subset():
    """Download COCO person/vehicle subset using fiftyone (offline-friendly alternative)."""
    print("\n📥 Tải COCO subset cho person, car, truck, motorcycle...")
    
    coco_dir = DATASETS_DIR / "coco_subset"
    os.makedirs(coco_dir, exist_ok=True)
    
    try:
        # Try using ultralytics built-in COCO download 
        from ultralytics import YOLO
        
        # Create a minimal dataset yaml for COCO subset
        coco_yaml = {
            "path": str(coco_dir),
            "train": "images/train",
            "val": "images/val",
            "names": {0: "person", 1: "car", 2: "truck", 3: "motorcycle"}
        }
        
        yaml_path = coco_dir / "coco_subset.yaml"
        with open(yaml_path, "w") as f:
            yaml.dump(coco_yaml, f)
        
        print("  ℹ️ COCO subset: Sử dụng pretrained COCO weights thay vì download riêng.")
        print("  → Model YOLOv11 đã được pretrain trên COCO (person, car, truck, motorcycle)")
        print("  → Chỉ cần fine-tune thêm license_plate + door classes")
        return True
    except Exception as e:
        print(f"  ⚠️ {e}")
        return False


def merge_datasets():
    """Merge downloaded datasets into a single unified dataset."""
    print("\n🔄 Đang merge datasets...")
    
    for split in ["train", "valid", "test"]:
        os.makedirs(MERGED_DIR / "images" / split, exist_ok=True)
        os.makedirs(MERGED_DIR / "labels" / split, exist_ok=True)

    total_images = 0
    
    # Process each downloaded dataset
    for ds_folder in DATASETS_DIR.iterdir():
        if not ds_folder.is_dir() or ds_folder.name == "merged_dataset":
            continue
        
        # Find matching config
        ds_config = None
        for cfg in ROBOFLOW_DATASETS:
            if cfg["name"].replace(" ", "_") in ds_folder.name:
                ds_config = cfg
                break
        
        if not ds_config:
            print(f"  ⏭ Bỏ qua: {ds_folder.name} (không tìm thấy config)")
            continue
        
        print(f"  📂 Processing: {ds_folder.name}")
        
        # Read source data.yaml
        source_yaml_path = ds_folder / "data.yaml"
        source_classes = {}
        if source_yaml_path.exists():
            with open(source_yaml_path) as f:
                src_cfg = yaml.safe_load(f)
                names = src_cfg.get("names", {})
                if isinstance(names, list):
                    source_classes = {i: n for i, n in enumerate(names)}
                elif isinstance(names, dict):
                    source_classes = {int(k): v for k, v in names.items()}
        
        print(f"    Source classes: {source_classes}")
        
        class_map = ds_config["class_map"]
        
        for split in ["train", "valid", "test"]:
            img_dir = ds_folder / split / "images"
            lbl_dir = ds_folder / split / "labels"
            
            if not img_dir.exists():
                # Try alternative structure
                img_dir = ds_folder / "images" / split
                lbl_dir = ds_folder / "labels" / split
            
            if not img_dir.exists():
                continue
            
            for img_path in img_dir.glob("*"):
                if img_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                    continue
                
                # Find corresponding label
                lbl_name = img_path.stem + ".txt"
                lbl_path = lbl_dir / lbl_name
                
                if not lbl_path.exists():
                    continue
                
                # Remap labels
                new_lines = []
                with open(lbl_path) as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) < 5:
                            continue
                        src_cls = int(parts[0])
                        
                        # Map source class to target class
                        src_name = source_classes.get(src_cls, str(src_cls))
                        target_cls = None
                        
                        for key, val in class_map.items():
                            if str(src_cls) == str(key) or src_name.lower() == str(key).lower():
                                target_cls = val
                                break
                        
                        if target_cls is not None:
                            parts[0] = str(target_cls)
                            new_lines.append(" ".join(parts))
                
                if new_lines:
                    # Copy image
                    prefix = ds_folder.name[:8]
                    new_img_name = f"{prefix}_{img_path.name}"
                    dest_split = split if split != "valid" else "valid"
                    
                    shutil.copy2(img_path, MERGED_DIR / "images" / dest_split / new_img_name)
                    
                    with open(MERGED_DIR / "labels" / dest_split / f"{prefix}_{img_path.stem}.txt", "w") as f:
                        f.write("\n".join(new_lines) + "\n")
                    
                    total_images += 1

    # Create data.yaml for merged dataset
    data_yaml = {
        "path": str(MERGED_DIR),
        "train": "images/train",
        "val": "images/valid",
        "test": "images/test",
        "nc": len(TARGET_CLASSES),
        "names": TARGET_CLASSES,
    }
    
    yaml_path = MERGED_DIR / "data.yaml"
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(data_yaml, f, allow_unicode=True, default_flow_style=False)

    print(f"\n✅ Merge hoàn tất! Tổng: {total_images} ảnh")
    print(f"   Dataset: {MERGED_DIR}")
    print(f"   Config:  {yaml_path}")
    
    # Print stats
    for split in ["train", "valid", "test"]:
        img_count = len(list((MERGED_DIR / "images" / split).glob("*")))
        print(f"   {split}: {img_count} ảnh")


def train_model(epochs=100, batch=16, imgsz=640, resume=False):
    """Train YOLOv11 model on merged dataset."""
    from ultralytics import YOLO
    
    yaml_path = MERGED_DIR / "data.yaml"
    if not yaml_path.exists():
        print("❌ Chưa merge dataset! Chạy: python3 train_custom_model.py --merge")
        sys.exit(1)
    
    # Use YOLOv11n (nano) as base - good balance of speed/accuracy
    # Options: yolo11n.pt, yolo11s.pt, yolo11m.pt, yolo11l.pt, yolo11x.pt
    base_model = "yolo11n.pt"
    
    print(f"\n🚀 Bắt đầu training YOLOv11...")
    print(f"   Base model: {base_model}")
    print(f"   Dataset: {yaml_path}")
    print(f"   Epochs: {epochs}")
    print(f"   Batch size: {batch}")
    print(f"   Image size: {imgsz}")
    
    model = YOLO(base_model)
    
    results = model.train(
        data=str(yaml_path),
        epochs=epochs,
        batch=batch,
        imgsz=imgsz,
        patience=20,
        save=True,
        save_period=10,
        project=str(PROJECT_ROOT / "runs"),
        name="custom_detector",
        exist_ok=True,
        pretrained=True,
        optimizer="auto",
        lr0=0.01,
        lrf=0.01,
        mosaic=1.0,
        mixup=0.1,
        copy_paste=0.1,
        degrees=10.0,
        translate=0.2,
        scale=0.5,
        flipud=0.0,
        fliplr=0.5,
        hsv_h=0.015,
        hsv_s=0.7,
        hsv_v=0.4,
        resume=resume,
    )
    
    print(f"\n✅ Training hoàn tất!")
    print(f"   Best model: runs/custom_detector/weights/best.pt")
    return results


def export_model():
    """Copy best model to models/ directory."""
    best_path = PROJECT_ROOT / "runs" / "custom_detector" / "weights" / "best.pt"
    if not best_path.exists():
        print("❌ Chưa train model! Chạy: python3 train_custom_model.py --train")
        sys.exit(1)
    
    dest = MODELS_DIR / "custom_detector.pt"
    shutil.copy2(best_path, dest)
    print(f"✅ Model đã export: {dest}")
    print(f"   → Cập nhật file main.py để load model mới: 'models/custom_detector.pt'")


def main():
    parser = argparse.ArgumentParser(description="Train custom YOLOv11 model")
    parser.add_argument("--download", action="store_true", help="Download datasets from Roboflow")
    parser.add_argument("--api-key", type=str, default="", help="Roboflow API key")
    parser.add_argument("--merge", action="store_true", help="Merge downloaded datasets")
    parser.add_argument("--train", action="store_true", help="Train model")
    parser.add_argument("--export", action="store_true", help="Export best model")
    parser.add_argument("--epochs", type=int, default=100, help="Training epochs (default: 100)")
    parser.add_argument("--batch", type=int, default=16, help="Batch size (default: 16)")
    parser.add_argument("--imgsz", type=int, default=640, help="Image size (default: 640)")
    parser.add_argument("--resume", action="store_true", help="Resume training")
    parser.add_argument("--all", action="store_true", help="Download + Merge + Train + Export")
    
    args = parser.parse_args()
    
    if args.all or args.download:
        api_key = args.api_key or os.environ.get("ROBOFLOW_API_KEY", "")
        if not api_key:
            print("⚠️ Cần Roboflow API key!")
            print("  1. Đăng ký miễn phí: https://app.roboflow.com")
            print("  2. Lấy API key: Settings → API Keys")
            print("  3. Chạy: python3 train_custom_model.py --download --api-key YOUR_KEY")
            print("  Hoặc: export ROBOFLOW_API_KEY=YOUR_KEY")
            if not args.all:
                sys.exit(1)
        else:
            download_datasets(api_key)
            download_coco_subset()
    
    if args.all or args.merge:
        merge_datasets()
    
    if args.all or args.train:
        train_model(epochs=args.epochs, batch=args.batch, imgsz=args.imgsz, resume=args.resume)
    
    if args.all or args.export:
        export_model()
    
    if not any([args.download, args.merge, args.train, args.export, args.all]):
        parser.print_help()
        print("\n📋 Quy trình training:")
        print("  Bước 1: python3 train_custom_model.py --download --api-key YOUR_KEY")
        print("  Bước 2: python3 train_custom_model.py --merge")
        print("  Bước 3: python3 train_custom_model.py --train --epochs 100")
        print("  Bước 4: python3 train_custom_model.py --export")
        print("\n  Hoặc: python3 train_custom_model.py --all --api-key YOUR_KEY")


if __name__ == "__main__":
    main()
