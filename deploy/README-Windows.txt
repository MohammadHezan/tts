MEETING INTERPRETER - WINDOWS
=============================

An AI interpreter that joins your Google Meet / Teams call as its own
participant and speaks each sentence in the other language
(English <-> Arabic), so everyone in the call hears it.

NEEDS: Windows 10/11, 16 GB of memory, about 50 GB of free disk space, internet,
       and - for the full accuracy - an NVIDIA graphics card with 12 GB (an RTX 3060
       was tested). Close games and video editors first: they use the card.


1. DOCKER DESKTOP (once)
   - Install it from https://www.docker.com/products/docker-desktop/
   - RESTART THE PC after installing.
   - Open Docker Desktop. Click "Accept" on the agreement (signing in is
     optional - skip it). Wait until the bottom-left corner says
     "Engine running".

   Stuck on "Engine starting"?
   - Open PowerShell as administrator, run:  wsl --update
     then restart the PC.
   - Task Manager -> Performance -> CPU: "Virtualization" must say Enabled.
     If not, turn it on in the PC's BIOS/UEFI settings
     ("SVM" on AMD, "VT-x" / "Intel Virtualization" on Intel).


2. START
   Double-click  "Start Interpreter"
   - If Windows asks "Do you want to run this file?", click Run
     (or "More info" -> "Run anyway").
   - It may ask two questions: answer Y to both (more memory for Docker,
     and letting your phone reach this PC).
   - FIRST TIME ONLY, it asks you to choose a dashboard username and
     password (at least 8 characters). Only you can send the bot into a
     meeting with them. Only a scrambled hash is saved (the .env file in
     "app"); to change it, delete that file and start again.
   - The first start downloads the programs (about 15 GB) and the models
     (about 6 GB, checked and unpacked by itself; if interrupted, start again
     and it continues). Allow 30-60 minutes. Leave the window open. After
     that it starts in about a minute.
   - When it's ready, your browser opens the login page.


3. USE IT
   1. Start a Google Meet (meet.google.com -> New meeting -> Start an
      instant meeting) and copy its link. Teams links work too. Zoom links
      don't yet: Zoom only lets bots in through a Zoom developer app.
   2. Log in, paste the link on the page, press "Send interpreter into
      meeting".
   3. In Meet, let "AI Interpreter" in.
   4. Talk normally. Each phrase appears in the MEETING CHAT as
      "[EN] what was said" then "[AR] its translation", and the bot speaks
      the translation once you stop talking. (The dashboard itself shows
      no text.)
   5. The dashboard buttons switch the voice (and each language's voice),
      the typed chat text, and pause the interpreter. Nothing can be
      switched from the meeting itself.
   6. When the meeting ends, a Word file with the notes of the meeting and
      each speaker's summary and sentences is written to
      app\meeting-data (and the "Download meeting document" button gives it).
   Two phones in one room? Use earbuds, or they hear each other.
   Something wrong? Double-click "Collect Logs" and send the logs.txt it writes.


STOP:  double-click  "Stop Interpreter"

The "app" folder holds the interpreter itself - no need to open it.
