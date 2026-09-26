"""Image provider adapters — one module per source, each implementing
ImageProvider (base.py). See §15.3 for the source-selection criteria.

KNOWN_PROVIDERS is the one list of names images.preferred_sources can
legally contain. Both the builder below and doctor's
image-provider-configuration check read it, so a name that silently
falls out of the config (build_providers() drops what it doesn't
recognize) is a name doctor can still warn about.
"""
from __future__ import annotations

from backyard_bird.images.providers.base import ImageProvider


def _registry() -> dict[str, type[ImageProvider]]:
    """Imported inside the function: the provider modules pull in
    requests and friends, and neither doctor's name check nor a plain
    `from backyard_bird.images.providers import KNOWN_PROVIDERS`
    should pay for that.
    """
    from backyard_bird.images.providers.inaturalist import INaturalistProvider
    from backyard_bird.images.providers.wikimedia import WikimediaCommonsProvider

    return {
        "wikimedia_commons": WikimediaCommonsProvider,
        "inaturalist": INaturalistProvider,
    }


KNOWN_PROVIDERS: tuple[str, ...] = ("wikimedia_commons", "inaturalist")

DEFAULT_PROVIDER = "wikimedia_commons"


def build_providers(preferred_sources: list[str]) -> list[ImageProvider]:
    """Builds the provider list from images.preferred_sources (§18.1),
    so config controls which sources get tried and in what order — not
    something hardcoded at a call site. Falls back to Wikimedia alone
    if the config list is empty or names nothing this codebase
    implements; `doctor` reports that case rather than leaving the
    fallback silent.
    """
    registry = _registry()
    providers = [registry[name]() for name in preferred_sources if name in registry]
    return providers or [registry[DEFAULT_PROVIDER]()]
