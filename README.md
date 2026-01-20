

![MoonBeamer Logo.](https://github.com/printerpr0n/MoonBeamer/blob/main/MoonBeamerLogo-TransparentBG.jpeg))


# MoonBeamer
Camera Streaming Component for Klipper / Moonraker

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


## Download MoonBeamer
1. - Us your preferred method to download the repo. 
	- Use the green "Code" button in the top right and choose "Download Zip".
		Use your favorite SSH/SCP program to transfer to your 3D printer host Raspberry Pi / Print Server.
	- Use curl to download the project files:
		"curl -L -O https://github.com/printerpr0n/MoonBeamer/archive/refs/heads/main.zip"
	- Use wget to down the project file:
		"wget https://github.com/printerpr0n/MoonBeamer/archive/refs/heads/main.zip"
	- Use git to clone the repo:
		"git clone https://github.com/printerpr0n/MoonBeamer.git"

The last method gives you an uncompressed folder. The first 3 methods require you to unzip the file into your
 user directory which klipper/moonraker is running under and installed. For most Raspberry Pi installes this
 will be "pi" or the user you created at setup.

Unzipping the file should leave you with a "MoonBeamer-main" folder.
	- Rename the folder with the following command:
		"mv MoonBeamer-main MoonBeamer"


## Install Instructions

 - Find your Moonraker source directory. Common example: /home/<user>/moonraker/moonraker/components/

 - The main component (rtmp_streamer.py) needs to be placed into the moonraker components directory.
   This may be different depending on your distro / install / user.

--------
Step 1:	Copy component into Moonraker
	Change into the MoonBeamer Directory: "cd MoonBeamer"
	Replace the <user> with your current username klipper/moonraker was installed under.
	Run command:
		"cp moonbeamer/rtmp_streamer.py /home/<user>/moonraker/moonraker/components/rtmp_streamer.py"

--------
Step 2: Restart Moonraker service with the command: "sudo systemctl restart moonraker"
	(Optional, you can also just reboot)

--------
Step 3: Add MoonBeamer config to moonraker.conf
	
	- Add config to the moonraker.conf. Copy and paste the contents of the moonbeamer.cfg file into the
	  end of your moonranker.conf file. 
        You will need to replace all the <user> entried with the username Klipper/Moonraker was installed under.

	- Alternatively you can copy the entire moonbeamer.cfg file to your config directory where you your moonraker.conf/printer.cfg files are stored and add an [include] to the moonraker.conf file instead of copying and pasting the contrents into the moonraker.conf file. This keeps your moonraker.conf file cleaner and allows for easier debuging.
		- Run command: "cp examples/moonraker.conf.d/moonbeamer.cfg ~/printer_data/config/"
		- Then add "[include moonbeamer.cfg]" to your moonraker.conf file.
		- As with previous method be sure to go through the moonbeamer.cfg file and replace all <user> entires with your username or the directory Klipper/Moonraker is installed under.

-------- 
Step 4: Restart Moonraker service with the command: "sudo systemctl restart moonraker" (Optional, you can also just reboot)

--------
Step 5: Add macros (Mainsail/Fluidd buttons) Copy the example macro file into your Klipper config directory and include it. Example (Klipper config dir often /home/<user>/printer_data/config/):

	Run command:  "cp examples/klipper_macros/moonbeamer_macros.cfg /home/<user>/printer_data/config/moonbeamer_macros.cfg"
	
--------
Step 6: Then add to your printer.cfg or mainsail.cfg

	Copy and paste the line below:
		[include moonbeamer_macros.cfg]

--------
Step 7:  Create the media folder and copy the example intro/outro/background media files.
	Run the folloing commands:
		- "cd ~/MoonBeamer"		(To confirm you are in the correct directory)
		- "mkdir ../media"		(Creates media folder)
		- "cp examples/media/*.* ~/media/"


Step 9:  Reboot!


Done!

Notes: First run the script will process your intro/outro files and convert them to the same format as your stream set in the moonbeamer.cfg/moonraker.conf options. If you do not have any intro/outro files disable the option.

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
GNU General Public License v3.0

