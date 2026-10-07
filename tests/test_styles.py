import pytest

from comfy_suite.comfy import ServerModels
from comfy_suite.styles import LoraRef, Style, StyleLibrary, guess_architecture


def test_builtin_styles_load(tmp_path):
    lib = StyleLibrary(tmp_path)
    assert len(lib.styles) >= 4
    assert lib.get("Flux").architecture == "flux"


def test_prompt_template():
    s = Style(style_prompt="photo, {prompt}, sharp")
    assert s.apply_prompt("a cat") == "photo, a cat, sharp"
    assert s.apply_prompt("") == "photo, sharp"
    assert Style(style_prompt="watercolor").apply_prompt("a dog") == "watercolor, a dog"


def test_guess_architecture():
    assert guess_architecture("flux1-dev-fp8.safetensors") == "flux"
    assert guess_architecture("juggernautXL_v9.safetensors") == "sdxl"
    assert guess_architecture("dreamshaper_8.safetensors") == "sd15"
    assert guess_architecture("mystery.ckpt") is None


def test_checkpoint_resolution():
    models = ServerModels(checkpoints=["sd15_dreamshaper.safetensors", "sdxl_base.safetensors"])
    assert Style(architecture="sdxl").resolve_checkpoint(models) == "sdxl_base.safetensors"
    assert Style(architecture="auto").resolve_checkpoint(models) == "sd15_dreamshaper.safetensors"
    with pytest.raises(ValueError):
        Style(checkpoint="missing.safetensors").resolve_checkpoint(models)
    with pytest.raises(ValueError):
        Style().resolve_checkpoint(ServerModels())


def test_user_styles_override_and_round_trip(tmp_path):
    lib = StyleLibrary(tmp_path)
    flux = lib.get("Flux")
    flux.steps = 4
    flux.loras = [LoraRef("detail.safetensors", 0.7)]
    lib.save(flux)
    again = StyleLibrary(tmp_path).get("Flux")
    assert again.steps == 4 and again.loras[0].strength == 0.7
    assert lib.delete(again)
    assert StyleLibrary(tmp_path).get("Flux").steps != 4

    new = Style(name="Mine")
    lib.save(new)
    assert "Mine" in StyleLibrary(tmp_path).names()
