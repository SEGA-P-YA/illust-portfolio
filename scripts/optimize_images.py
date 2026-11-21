#!/usr/bin/env python3
"""画像自動最適化スクリプト。

assets/originals/ に置かれた原画（アップロードされたままの画像）から、
サイト掲載用の WebP 画像を assets/images/ に生成する。

- メイン画像: 長辺 2048px 以内、WebP 品質 82
- サムネイル: 長辺 800px 以内、WebP 品質 82
- 原画は一切変更・削除しない
- 処理済みの判定は原画の SHA-256 ハッシュで行う（manifest.json）。
  ハッシュが同じ原画は再処理しないため、再圧縮による画質劣化は起きない
- SVG / GIF は変換対象外（そのまま使われる）
"""
import hashlib
import json
import os
import sys
import unicodedata
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
ORIGINALS = ROOT / "assets" / "originals"
OUT_DIR = ROOT / "assets" / "images"
MANIFEST_PATH = OUT_DIR / "manifest.json"

RASTER_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".ppm", ".pgm"}
MAIN_MAX = 2048
THUMB_MAX = 800
WEBP_QUALITY = 82


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def resized(img: Image.Image, max_edge: int) -> Image.Image:
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= max_edge:
        return img.copy()
    scale = max_edge / long_edge
    return img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {}
    if MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("manifest.json が壊れているため作り直します", file=sys.stderr)
            manifest = {}

    summary = []
    used_bases = {}
    current_keys = set()

    originals = sorted(p for p in ORIGINALS.rglob("*") if p.is_file()) if ORIGINALS.exists() else []
    for original in originals:
        rel = original.relative_to(ROOT).as_posix()
        ext = original.suffix.lower()
        if ext in (".svg", ".gif"):
            summary.append(f"SKIP (変換対象外): {rel}")
            continue
        if ext not in RASTER_EXTS:
            summary.append(f"SKIP (未対応形式): {rel}")
            continue

        # 生成ファイル名は NFC に統一する。macOS / iOS からアップロードされた
        # 原画はファイル名が NFD（濁点が分離した形）になっていることがあり、
        # サイト側が要求する NFC の URL と一致せず画像が 404 になるため。
        base = unicodedata.normalize("NFC", original.stem)
        # 同名ファイルが複数ある場合はハッシュで一意化する
        digest = sha256_of(original)
        if base in used_bases and used_bases[base] != rel:
            base = f"{base}-{digest[:6]}"
        used_bases[base] = rel
        current_keys.add(rel)

        main_path = OUT_DIR / f"{base}-2048.webp"
        thumb_path = OUT_DIR / f"{base}-800.webp"
        entry = manifest.get(rel)
        if entry and entry.get("sha256") == digest and main_path.exists() and thumb_path.exists():
            summary.append(f"UNCHANGED: {rel}")
            continue

        with Image.open(original) as src:
            img = ImageOps.exif_transpose(src)
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
            main_img = resized(img, MAIN_MAX)
            main_img.save(main_path, "WEBP", quality=WEBP_QUALITY, method=6)
            thumb_img = resized(img, THUMB_MAX)
            thumb_img.save(thumb_path, "WEBP", quality=WEBP_QUALITY, method=6)

        manifest[rel] = {
            "sha256": digest,
            "original_bytes": original.stat().st_size,
            "main": {
                "path": main_path.relative_to(ROOT).as_posix(),
                "width": main_img.size[0],
                "height": main_img.size[1],
                "bytes": main_path.stat().st_size,
            },
            "thumbnail": {
                "path": thumb_path.relative_to(ROOT).as_posix(),
                "width": thumb_img.size[0],
                "height": thumb_img.size[1],
                "bytes": thumb_path.stat().st_size,
            },
        }
        summary.append(
            f"OPTIMIZED: {rel} ({original.stat().st_size} bytes) -> "
            f"main {main_path.stat().st_size} bytes {main_img.size[0]}x{main_img.size[1]}, "
            f"thumb {thumb_path.stat().st_size} bytes"
        )

    # 原画が削除された作品の生成物だけを片付ける（原画には触れない）
    for rel in list(manifest.keys()):
        if rel not in current_keys:
            for kind in ("main", "thumbnail"):
                out = ROOT / manifest[rel][kind]["path"]
                if out.exists():
                    out.unlink()
            summary.append(f"CLEANUP (原画が無くなった生成物を削除): {rel}")
            del manifest[rel]

    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    text = "\n".join(summary) if summary else "対象画像はありませんでした"
    print(text)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write("## 画像自動最適化の結果\n\n```\n" + text + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
