# Third-party notices

## Blackmagic DeckLink SDK headers

`third_party/decklink-sdk/Win/include/` contains the unmodified Windows interface
definition and header files of the Blackmagic DeckLink SDK 15.3. The release DLL contains
code that MIDL generated from them, such as interface IDs.

Copyright (c) 2025 Blackmagic Design. They are distributed under the licence stated at the
top of each file, which refers to the DeckLink SDK End User License Agreement
(<https://www.blackmagicdesign.com/EULA/DeckLinkSDK>). Clause 0.1 of that agreement
exempts the SDK's `/Win/Include`, `/Mac/Include` and `/Linux/Include` folders from its
use and redistribution restrictions. The rest of the SDK isn't included in this
repository. Download it from <https://www.blackmagicdesign.com/developer/>.

The headers are not covered by this project's GPL licence.

## Blackmagic Desktop Video

The DeckLink runtime (`DeckLinkAPI` COM components and driver) is proprietary software by
Blackmagic Design. It isn't included in this project, and each user installs it
separately with Desktop Video. See `LICENSE-EXCEPTION.txt`.

## Microsoft Visual C++ runtime

The DLL is linked with the static Microsoft Visual C++ runtime (`/MT`). This is permitted
by the Visual Studio licence terms for redistributable code.

## Trademarks

Blackmagic Design, DeckLink, Intensity, UltraStudio and Desktop Video are trademarks of
Blackmagic Design Pty. Ltd. Blender is a trademark of the Blender Foundation. Their use
here only describes compatibility. This project isn't affiliated with or endorsed by
Blackmagic Design or the Blender Foundation.
