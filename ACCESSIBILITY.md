# Accessibility

Stratos Poker Arena aims to be playable by as many people as possible, on phones and computers.

## Supported environments

- Current versions of Chrome, Edge, Firefox and Safari on desktop, Android and iOS.
- Phone (portrait and landscape) and desktop screen sizes.

## What works today

- All actions (Check, Call, Fold, Raise, join table, add/remove bots, reload) are real HTML buttons, so they can be reached with the keyboard (Tab / Enter) and are announced by screen readers.
- The page declares its language (`lang="en"`) and scales to the device width.
- Sounds only play after the player interacts with the page and are never needed to play; every sound has a visible equivalent (turn ring, dealer log, winner banner).
- A 60-second action clock gives time to decide before an automatic check/fold.

## Known limitations

- The table itself (cards, chips, seats) is drawn on a canvas, so screen readers cannot read the cards directly; the dealer log on the right gives a text record of the action.
- Some card and chip colours rely on colour; suit symbols are shown alongside.
- There are no dedicated keyboard shortcuts yet.

## Reporting a barrier

If something stops you from playing, open an issue with the **Bug report** template and add the label `accessibility`, describing your device, browser and any assistive technology you use.
