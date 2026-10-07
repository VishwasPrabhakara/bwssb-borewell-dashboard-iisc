#!/usr/bin/env python3
"""Convert Bangalore DEM TIFF into Mapbox terrain-RGB XYZ tiles for MapLibre 3D."""
import json, numpy as np
from pathlib import Path
import rasterio
from rasterio.warp import reproject, Resampling, transform_bounds
from rasterio.transform import from_bounds
import mercantile
from PIL import Image

SRC   = Path(r"C:\Users\ADMIN\OneDrive - Manipal Academy of Higher Education\Vishwas_IISC_Internship_Files\Codes\BWSSB_Aiml_model\Master_data\Bangalore_DEM.tif")
ROOT  = Path(__file__).resolve().parents[2]
OUT   = ROOT / "outputs" / "dem_tiles"
META  = ROOT / "outputs" / "bangalore_dem_tiles_meta.json"
MIN_Z, MAX_Z = 10, 13      # 10-13 is plenty for a city DEM; z=14 would 4x tile count
TILE  = 256

def encode_terrain_rgb(elev):
    """Mapbox terrain-rgb: height = -10000 + ((R*65536 + G*256 + B) * 0.1)"""
    v = ((elev + 10000.0) * 10.0).astype(np.int64)
    v = np.clip(v, 0, 256**3 - 1)
    r = ((v >> 16) & 0xFF).astype(np.uint8)
    g = ((v >>  8) & 0xFF).astype(np.uint8)
    b = ( v        & 0xFF).astype(np.uint8)
    a = np.where(np.isnan(elev), 0, 255).astype(np.uint8)
    return np.stack([r, g, b, a], axis=-1)

print(f"[dem_tiles] opening {SRC.name}")
with rasterio.open(SRC) as src:
    nodata = src.nodata if src.nodata is not None else -9999
    if src.crs.to_string() != "EPSG:4326":
        west, south, east, north = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
    else:
        west, south, east, north = src.bounds
    print(f"[dem_tiles] bounds W={west:.4f} S={south:.4f} E={east:.4f} N={north:.4f}")

    total = 0
    for z in range(MIN_Z, MAX_Z + 1):
        tiles = list(mercantile.tiles(west, south, east, north, z))
        print(f"[dem_tiles] z={z}: {len(tiles)} tiles")
        for t in tiles:
            tb = mercantile.bounds(t)
            dst = np.full((TILE, TILE), nodata, dtype=np.float32)
            dst_tf = from_bounds(tb.west, tb.south, tb.east, tb.north, TILE, TILE)
            reproject(
                source=rasterio.band(src, 1), destination=dst,
                src_transform=src.transform, src_crs=src.crs,
                dst_transform=dst_tf, dst_crs="EPSG:4326",
                resampling=Resampling.bilinear,
                src_nodata=nodata, dst_nodata=nodata)
            elev = np.where(dst == nodata, np.nan, dst)
            if np.all(np.isnan(elev)):
                continue
            rgba = encode_terrain_rgb(elev)
            d = OUT / str(z) / str(t.x); d.mkdir(parents=True, exist_ok=True)
            Image.fromarray(rgba, mode="RGBA").save(d / f"{t.y}.png", optimize=True)
            total += 1
        print(f"  z={z} wrote {total} total")
print(f"[dem_tiles] done - {total} tiles")

META.write_text(json.dumps({
    "west": west, "south": south, "east": east, "north": north,
    "minzoom": MIN_Z, "maxzoom": MAX_Z,
    "tile_url": "dem_tiles/{z}/{x}/{y}.png",
    "encoding": "mapbox"
}, indent=2))
print(f"[dem_tiles] wrote {META}")