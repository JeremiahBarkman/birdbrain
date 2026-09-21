"""Slideshow generation (§16, §29 Phase 5).

renderer.py: turns one species' already-cropped 1920x1080 base image
(images/cache.py's build_optimized_frame_image output — cropping and
optimization are that module's job, not this one's) plus its
aggregated daily numbers into one finished JPEG slide with text
overlays (§16.2/16.3/16.4).

builder.py: ties aggregation.service + images + renderer.py + ordering
(§16.5) together into a real SlideshowManifest and slideshows/
slideshow_items rows (build_daily_slideshow()) — the piece Phase 5 was
missing until it was written; renderer.py was usable and testable on
its own before this existed, same reasoning as frame/manifest.py being
split out of the frame adapters that needed its shape before Phase 5
did.
"""
