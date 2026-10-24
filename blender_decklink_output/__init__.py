# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Thomas Doukinitsas

"""Blender Live Camera Output for Blackmagic Design DeckLink.

Sends a scene camera's picture live to a Blackmagic Design DeckLink card.

Output Properties > Live Camera Output. Each output picks a device and video mode,
a camera, a shading mode, fit / fill / distort reframing, overlays and the compositor.
The card runs on its own clock, so neither the resolution nor the frame rate has to match
the scene. Documentation: https://github.com/tdoukinitsas/blender-decklink-output
"""

from . import engine, ops, props, ui

_MODULES = (props, ops, ui, engine)


def register():
    for module in _MODULES:
        module.register()


def unregister():
    for module in reversed(_MODULES):
        module.unregister()
