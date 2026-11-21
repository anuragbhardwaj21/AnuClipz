# AnuClipz

Paste a YouTube link. AnuClipz reads the captions with local tools, sends only a short list to your model, and comes back with moments worth posting.

The reel is a tall frame: a title on top, the gameplay sharp in the middle, and the same picture blurred behind it. The spoken line sits at the bottom. The model also writes a post title meant to get the tap.

## Run

One command after a fresh clone. It installs the page, the Python tools, and, on an Apple silicon Mac, the caption ffmpeg, then opens the app.

```bash
git clone https://github.com/anuragbhardwaj21/AnuClipz.git && cd AnuClipz && npm start
```

When the folder is already on the machine, run `npm start` inside it.

Open http://127.0.0.1:5174

The first run installs the page, a small Python environment, and, on an Apple silicon Mac, the caption ffmpeg.

## What spends tokens

Local tools open the link and read every caption word. That part is free. The model only sees about eight short moments, with thinking turned off, and returns the cuts, the on-video line, and the post title. The page shows prompt, reply, thinking, and total tokens while the call is in flight.

Pick the light model in the list when you want the smallest bill. If a model is busy or slow, choose another and run it again.

## Keys

Google AI Studio and ChatGPT can pick clips. The key stays in the browser tab until you close it. It is not written into the project.
