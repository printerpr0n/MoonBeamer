# MoonBeamer 🚀
**MoonBeamer** is a Moonraker component for Klipper that automatically streams your printer webcam to an RTMP endpoint (YouTube, NGINX-RTMP, etc.) when a print starts — and stops when the print ends.

It supports:
- Auto-start streaming on `printing`
- Auto-stop streaming after print completes/errors (with configurable delay)
- Optional background music (looped)
- Optional intro/outro videos (auto-normalized to your stream settings)
- On-stream text overlay (e.g., “Printing”)

> Project name: **MoonBeamer**  
> Component name: `rtmp_streamer`  
> API endpoint: `/server/rtmp_streamer`

---

## Requirements
- Moonraker + Klipper (Mainsail/Fluidd supported via macros)
- `ffmpeg` and `ffprobe` installed on the host running Moonraker

### Install ffmpeg / ffprobe (Debian/Raspberry Pi OS)
```bash
sudo apt update
sudo apt install -y ffmpeg




## Install Instructions

 - Find your Moonraker source directory. Common example: /home/<user>/moonraker/moonraker/components/


 - The main component (rtmp_streamer.py) needs to be placed into the moonraker components directory. This may be different depending on your distro / install / user.

--------
Step 1:	Copy component into Moonraker

	Run command:  sudo cp moonbeamer/rtmp_streamer.py /home/<user>/moonraker/moonraker/components/rtmp_streamer.py

--------
Step 2: Restart Moonraker service. (Optional, you can also just reboot)

--------
Step 3: Add MoonBeamer config to moonraker.conf
	
	Add config to the moonraker.conf. Copy and paste the contents of the moonbeamer.cfg file into the end of your moonranker.conf file. 
        You will need to replace the <user> with the username Moonraker was installed under.

-------- 
Step 4: Restart Moonraker (Optional, you can also just reboot)

--------
Step 5: Add macros (Mainsail/Fluidd buttons) Copy the example macro file into your Klipper config directory and include it. Example (Klipper config dir often /home/<user>/printer_data/config/):

	Run command:  cp examples/klipper_macros/moonbeamer_macros.cfg /home/<user>/printer_data/config/moonbeamer_macros.cfg
	
--------
Step 6: Then add to your printer.cfg or mainsail.cfg

	Copy and paste the line below:
		[include moonbeamer_macros.cfg]

--------
Step 7:  Reboot!


Done!



#------------------Additional Info------------------#

Usage
API

Status:
GET /server/rtmp_streamer

Commands:
POST /server/rtmp_streamer?op=start|stop|enable|disable|intro_enable|intro_disable|outro_enable|outro_disable|prepare_media

Example:

curl -s http://127.0.0.1:7125/server/rtmp_streamer
curl -s -X POST "http://127.0.0.1:7125/server/rtmp_streamer?op=start"
curl -s -X POST "http://127.0.0.1:7125/server/rtmp_streamer?op=stop"
Logs

ffmpeg stderr is written to:

/tmp/rtmp_streamer_ffmpeg.log (default)





#------------------Troubleshooting------------------#

No stream / no log file

Ensure Moonraker successfully loaded the component:

curl -s http://127.0.0.1:7125/server/rtmp_streamer

--
Check Moonraker service log:

sudo journalctl -u moonraker -n 200 --no-pager

--
Slow / “format” warnings on YouTube

Reduce camera resolution (MJPEG 1080p is CPU-heavy)

Try preset: ultrafast

Lower bitrate

--
FIFO overwrite prompt / “already exists”

MoonBeamer uses a FIFO at:
<media_cache_dir>/live_segment.ts
It should be recreated automatically each start. If permissions are wrong:

sudo mkdir -p /home/<user>/rtmp_streamer_cache
sudo chown -R <user>:<user> /home/<user>/rtmp_streamer_cache
chmod 775 /home/<user>/rtmp_streamer_cache

--


License
GPL

