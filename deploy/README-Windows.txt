MEETING INTERPRETER - WINDOWS
=============================

An AI interpreter that joins your Google Meet / Teams call as its own
participant and speaks each sentence in the other language
(English <-> Arabic), so everyone in the call hears it.

NEEDS: Windows 10/11, 16 GB of memory, about 20 GB of free disk space.


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
   - The first start downloads about 15 GB and can take 20-40 minutes.
     Leave the window open. After that it starts in about a minute.
   - When it's ready, your browser opens the interpreter page.


3. USE IT
   1. Start a Google Meet and copy its link.
   2. On your phone: Interpreter app -> Meeting Bot. It finds this PC by
      itself (same Wi-Fi). Paste the link, tap "Send interpreter into
      meeting". (Or paste it on the page that opened on this PC.)
   3. In Meet, let "AI Interpreter" in.
   4. Talk. After each sentence the interpreter says it in the other
      language, about 10-15 seconds later.


STOP:  double-click  "Stop Interpreter"

The "app" folder holds the interpreter itself - no need to open it.
