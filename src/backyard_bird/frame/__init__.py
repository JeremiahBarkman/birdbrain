"""Photo-frame delivery (§17, §29 Phase 6).

manifest.py: the shape a slideshow builder hands to an adapter.
base.py: the PhotoFrameAdapter interface every delivery method
implements (§17.4).
adapters/: one module per delivery method. local_export.py (§17.5) is
the only one that must always exist and always work (§30 rule 29:
"Preserve a manual export path even after automated delivery is
implemented").
service.py: picks the configured adapter from photo_frame.adapter.

The slideshow builder itself lives in backyard_bird.slideshow (§29
Phase 5 — daily aggregation, 1920x1080 rendering, the manifest a real
one of these gets built from). This package stays deliberately
self-contained from it: manifest.py defines the manifest shape the
interface commits to (§17.4), which is what let the adapters be built
and tested before Phase 5 landed, and still keeps them testable
without rendering a real day's slides.

Still missing above this package (§29 Phase 7): nothing calls
publish_slideshow() on a schedule — `frame test` is the only caller.
"""
