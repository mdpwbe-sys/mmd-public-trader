#!/usr/bin/env python3
import sys, os
from playwright.sync_api import sync_playwright

inp = os.path.abspath(sys.argv[1])
out = os.path.abspath(sys.argv[2])
w = int(sys.argv[3]) if len(sys.argv) > 3 else 1280
h = int(sys.argv[4]) if len(sys.argv) > 4 else 800

with sync_playwright() as p:
    b = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
    pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
    pg.goto("file://" + inp, wait_until="networkidle")
    pg.wait_for_timeout(900)
    pg.screenshot(path=out, full_page=False)
    b.close()
print("SHOT_SAVED", out)
