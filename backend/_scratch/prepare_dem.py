#!/usr/bin/env python3
"""Convert Bangalore DEM TIFF to a web-friendly hillshade + colored elevation PNG.
Run once. Produces outputs/bangalore_dem.png and outputs/bangalore_dem_bounds.json
that the cluster map overlays as a toggleable layer.
"""
import json, numpy as np
from pathlib import Path
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from PIL import Image

SRC = Path(r"C:\Users\ADMIN\OneDrive - Manipal Academy of Higher Education\Vishwas_IISC_Internship_Files\Codes\BWSSB_Aiml_model\Master_data\Bangalore_DEM.tif")
ROOT    = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "outputs"; OUT_DIR.mkdir(exist_ok=True)
PNG     = OUT_DIR / "bangalore_dem.png"
META    = OUT_DIR / "bangalore_dem_bounds.json"

def hillshade(dem, azdeg=315, altdeg=45, zfactor=1.0):
    """Standard hillshade (0-255)."""
    x, y = np.gradient(dem)
    slope = np.pi/2 - np.arctan(zfactor * np.hypot(x, y))
    aspect = np.arctan2(-x, y)
    azrad = np.radians(360.0 - azdeg + 90.0)
    altrad = np.radians(altdeg)
    shaded = (np.sin(altrad)*np.sin(slope) +
              np.cos(altrad)*np.cos(slope)*np.cos(azrad - aspect))
    return (255 * np.clip(shaded, 0, 1)).astype(np.uint8)

def elev_colormap(dem, lo, hi):
    """Terrain colormap: green low -> yellow -> brown -> white high."""
    stops = [(0.00, (56, 110, 70)),    # dark green (valleys)
             (0.25, (120, 160, 90)),   # light green
             (0.50, (200, 180, 110)),  # tan
             (0.75, (150, 110, 70)),   # brown
             (1.00, (240, 240, 240))]  # near-white (peaks)
    t = np.clip((dem - lo) / max(hi - lo, 1e-9), 0, 1)
    r = np.zeros_like(t); g = np.zeros_like(t); b = np.zeros_like(t)
    for i in range(len(stops)-1):
        t0, c0 = stops[i]; t1, c1 = stops[i+1]
        m = (t >= t0) & (t <= t1)
        f = (t[m] - t0) / max(t1 - t0, 1e-9)
        r[m] = c0[0] + f*(c1[0]-c0[0])
        g[m] = c0[1] + f*(c1[1]-c0[1])
        b[m] = c0[2] + f*(c1[2]-c0[2])
    return np.stack([r, g, b], axis=-1).astype(np.uint8)

print(f"[dem] reading {SRC.name}")
with rasterio.open(SRC) as src:
    # Reproject to EPSG:4326 if not already
    if src.crs.to_string() != "EPSG:4326":
        print(f"[dem] reprojecting from {src.crs} to EPSG:4326")
        dst_tf, w, h = calculate_default_transform(
            src.crs, "EPSG:4326", src.width, src.height, *src.bounds)
        dem = np.zeros((h, w), dtype=np.float32)
        reproject(
            source=rasterio.band(src, 1), destination=dem,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=dst_tf, dst_crs="EPSG:4326",
            resampling=Resampling.bilinear)
        west, north = dst_tf * (0, 0)
        east, south = dst_tf * (w, h)
    else:
        dem = src.read(1).astype(np.float32)
        west, south, east, north = src.bounds

    nodata = src.nodata if src.nodata is not None else -9999
    mask = (dem == nodata) | ~np.isfinite(dem)
    valid = dem[~mask]
    if len(valid) == 0:
        raise SystemExit("No valid DEM pixels found")
    lo, hi = float(np.percentile(valid, 1)), float(np.percentile(valid, 99))
    print(f"[dem] shape={dem.shape} elev p1={lo:.0f}m p99={hi:.0f}m")
    print(f"[dem] bounds west={west:.4f} south={south:.4f} east={east:.4f} north={north:.4f}")

# Hillshade + elevation color blend
dem_filled = np.where(mask, lo, dem)
hs = hillshade(dem_filled, azdeg=315, altdeg=45, zfactor=2.0)
col = elev_colormap(dem_filled, lo, hi).astype(np.float32)
# Multiply blend: color * (hs/255), gives a terrain look
blend = (col * (hs[..., None] / 255.0)).astype(np.uint8)
alpha = np.where(mask, 0, 220).astype(np.uint8)
rgba = np.concatenate([blend, alpha[..., None]], axis=-1)

# Downsample if huge (keep <= ~2000 px on long side for web)
Image.MAX_IMAGE_PIXELS = None
img = Image.fromarray(rgba, mode="RGBA")
max_side = 2000
if max(img.size) > max_side:
    scale = max_side / max(img.size)
    img = img.resize((int(img.size[0]*scale), int(img.size[1]*scale)), Image.LANCZOS)
    print(f"[dem] resized to {img.size}")
img.save(PNG, optimize=True)
META.write_text(json.dumps({
    "west": west, "south": south, "east": east, "north": north,
    "elev_min": lo, "elev_max": hi,
    "image": "bangalore_dem.png"
}, indent=2))
print(f"[dem] wrote {PNG} ({PNG.stat().st_size//1024} KB)")
print(f"[dem] wrote {META}")