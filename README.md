# vidcrop

GUI tool to trim (cut in time) and crop (cut in space) videos.

## Install

```bash
pip install git+https://github.com/YOUR_USERNAME/vidcrop.git
```

## Update

```bash
pip install --upgrade git+https://github.com/YOUR_USERNAME/vidcrop.git
```

## Usage

```bash
vidcrop
```

## How to use

1. Click **Open Video** and select a video file
2. Scrub the slider to find your start point → click **Set Start Here**
3. Scrub to your end point → click **Set End Here**
4. Optionally drag a bounding box on the frame to crop spatially
5. Click **Trim & Crop** and choose an output path

## Dependencies

- `opencv-python`
- `Pillow`
- `tkinter` (ships with Python on most platforms)
