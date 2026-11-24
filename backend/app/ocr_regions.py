"""Assisted OCR of explicitly annotated image regions; output is evidence, never approval."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from app.services.ingestion_ocr import configure_tesseract, tesseract_config


def extract_regions(raw_dir: Path, regions: list[dict], *, lang="eng", psm=6, ocr_fn=None) -> list[dict]:
    from PIL import Image, ImageOps
    import pytesseract
    if ocr_fn is None:
        configure_tesseract()
        ocr_fn = lambda image: pytesseract.image_to_string(image, lang=lang, config=tesseract_config(psm))
    raw_dir = raw_dir.resolve(); output = []; seen = set()
    for spec in regions:
        region_id = spec.get("region_id")
        if not region_id or region_id in seen: raise ValueError("region_id must be unique and nonempty")
        seen.add(region_id)
        source = (raw_dir / spec["image"]).resolve()
        if not source.is_relative_to(raw_dir): raise ValueError("region image must stay inside raw_dir")
        box = spec.get("bbox")
        if (not isinstance(box, list) or len(box) != 4 or
            any(not isinstance(x, (int, float)) or not 0 <= x <= 1 for x in box) or
            not box[0] < box[2] or not box[1] < box[3]): raise ValueError("bbox must be normalized x0,y0,x1,y1 inside the image")
        if not spec.get("source_url") or not spec.get("item_hint"): raise ValueError("source_url and a manually transcribed item_hint are required")
        original = urlparse(spec["source_url"])
        if original.scheme not in {"http", "https"} or not original.netloc:
            raise ValueError("source_url must be an HTTP(S) provenance link")
        with Image.open(source) as im:
            im = ImageOps.exif_transpose(im); w,h = im.size
            cropped = im.crop(tuple(round(x*(w if i%2==0 else h)) for i,x in enumerate(box)))
            prepared = ImageOps.autocontrast(ImageOps.grayscale(cropped), cutoff=1)
        text = ocr_fn(prepared)
        candidates = sorted(set(int(x) for x in re.findall(r"(?<!\d)(\d{1,5})\s*(?:kcal|keal|calories)\b", text, re.I)))
        output.append(dict(region_id=region_id, source_image=source.name,
                           source_image_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                           source_url=spec["source_url"], bbox=box, item_hint=spec["item_hint"],
                           serving_hint=spec.get("serving_hint"), reference_record_id=spec.get("reference_record_id"),
                           ocr_text=text, calorie_candidates=candidates,
                           gram_candidates=[float(x) for x in re.findall(r"(?<!\d)(\d+(?:\.\d+)?)\s*g\b",text,re.I)],
                           verification_status="pending", notes="Region/item hints are manual annotations; OCR values still require source checking. No current price is established."))
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--raw-dir",type=Path,required=True);p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True);p.add_argument("--psm",type=int,choices=[3,6,11],default=6)
    a=p.parse_args()
    if a.output.exists(): raise SystemExit("Choose a new output path to preserve earlier evidence")
    rows=extract_regions(a.raw_dir,json.loads(a.manifest.read_text()),psm=a.psm)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(rows,indent=2,ensure_ascii=False))
    print(json.dumps({"regions":len(rows),"approved":0,"output":str(a.output)},indent=2))


if __name__ == "__main__": main()
