from backyard_bird.cli import _build_image_providers
from backyard_bird.images.providers.inaturalist import INaturalistProvider
from backyard_bird.images.providers.wikimedia import WikimediaCommonsProvider


def test_builds_providers_in_configured_order() -> None:
    providers = _build_image_providers(["inaturalist", "wikimedia_commons"])
    assert [type(p) for p in providers] == [INaturalistProvider, WikimediaCommonsProvider]


def test_unknown_source_names_are_skipped() -> None:
    providers = _build_image_providers(["flickr", "wikimedia_commons"])
    assert [type(p) for p in providers] == [WikimediaCommonsProvider]


def test_empty_config_falls_back_to_wikimedia() -> None:
    providers = _build_image_providers([])
    assert [type(p) for p in providers] == [WikimediaCommonsProvider]
