#!/usr/bin/env python3
"""
qr_generator.py — Generate high-resolution PNG QR codes for every registered slug.

Usage examples
--------------
# Generate QR codes for specific slugs:
    python qr_generator.py --base-url https://qr.yourdomain.com --slugs table_1 table_2

# Generate QR codes for ALL slugs currently in the database:
    python qr_generator.py --base-url https://qr.yourdomain.com --from-db

# Override output directory:
    python qr_generator.py --base-url https://qr.yourdomain.com --from-db --out ./print_ready

QR code settings
----------------
  Error correction: H  (30 % of the code can be obscured and still scan)
  box_size        : 12 pixels per module  (≈ 4 × 4 cm at 96 DPI)
  border          : 4 modules (quiet zone — required by the QR spec)
"""

import argparse
import os
import sys

try:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_H
except ImportError:
    sys.exit("qrcode is not installed. Run: pip install 'qrcode[pil]'")

try:
    from PIL import Image  # noqa: F401 — ensure Pillow is available
except ImportError:
    sys.exit("Pillow is not installed. Run: pip install Pillow")

DEFAULT_OUTPUT_DIR = "qr_codes"


def generate_qr(
    slug: str,
    base_url: str,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    box_size: int = 12,
    border: int = 4,
) -> str:
    """
    Generate a single QR code PNG for *slug* and save it to *output_dir*.

    Returns the absolute path to the saved file.
    """
    os.makedirs(output_dir, exist_ok=True)

    url = f"{base_url.rstrip('/')}/r/{slug}"

    qr = qrcode.QRCode(
        version=None,            # auto-select smallest version that fits
        error_correction=ERROR_CORRECT_H,
        box_size=box_size,
        border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    out_path = os.path.join(output_dir, f"{slug}.png")
    img.save(out_path)
    print(f"  ✓  {slug:<20}  →  {out_path}   ({url})")
    return out_path


def generate_from_db(base_url: str, output_dir: str = DEFAULT_OUTPUT_DIR) -> None:
    """Generate QR codes for every slug stored in the database."""
    try:
        from database import SessionLocal
        from models import QRLink
    except ImportError as exc:
        sys.exit(f"Cannot import database modules: {exc}")

    db = SessionLocal()
    try:
        links = db.query(QRLink).order_by(QRLink.slug).all()
        if not links:
            print("No slugs found in the database. Add some first with seed_data.py or the admin API.")
            return
        print(f"Generating {len(links)} QR code(s) into '{output_dir}/' …\n")
        for link in links:
            generate_qr(link.slug, base_url, output_dir)
    finally:
        db.close()

    print(f"\nDone. {len(links)} QR code(s) saved to '{os.path.abspath(output_dir)}'.")


def generate_from_slugs(
    slugs: list[str], base_url: str, output_dir: str = DEFAULT_OUTPUT_DIR
) -> None:
    print(f"Generating {len(slugs)} QR code(s) into '{output_dir}/' …\n")
    for slug in slugs:
        generate_qr(slug.strip(), base_url, output_dir)
    print(f"\nDone. {len(slugs)} QR code(s) saved to '{os.path.abspath(output_dir)}'.")


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate high-resolution QR codes for registered slugs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--base-url",
        required=True,
        metavar="URL",
        help="Base URL of your deployed service, e.g. https://qr.yourdomain.com",
    )
    parser.add_argument(
        "--slugs",
        nargs="+",
        metavar="SLUG",
        help="One or more slug names to generate QR codes for.",
    )
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="Generate QR codes for ALL slugs stored in the database.",
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUTPUT_DIR,
        metavar="DIR",
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--box-size",
        type=int,
        default=12,
        metavar="N",
        help="Pixels per QR module (default: 12).",
    )
    parser.add_argument(
        "--border",
        type=int,
        default=4,
        metavar="N",
        help="Quiet-zone border in modules (default: 4).",
    )

    args = parser.parse_args()

    if args.from_db:
        generate_from_db(args.base_url, args.out)
    elif args.slugs:
        generate_from_slugs(args.slugs, args.base_url, args.out)
    else:
        parser.print_help()
        sys.exit(1)
