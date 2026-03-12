import subprocess
import os
import signal
import sys
import threading
import collections

class BotController:
    def __init__(self, bot_script_path):
        self.bot_script_path = bot_script_path
        self.process = None
        self.log_buffer = collections.deque(maxlen=1000)
        self.is_running = False

    def start(self):
        if self.process and self.process.poll() is None:
            return False, "Bot is already running."
        
        try:
            # Start the bot script as a separate process
            # We use unbuffered output to capture logs in real-time
            self.process = subprocess.Popen(
                [sys.executable, "-u", self.bot_script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                preexec_fn=os.setsid if os.name != 'nt' else None
            )
            self.is_running = True
            
            # Start a thread to read logs
            threading.Thread(target=self._read_logs, daemon=True).start()
            return True, "Bot started successfully."
        except Exception as e:
            return False, f"Failed to start bot: {str(e)}"

    def stop(self):
        if not self.process:
            return False, "Bot is not running."
        
        try:
            # Send SIGTERM to the process group
            if os.name != 'nt':
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            else:
                self.process.terminate()
            
            self.process.wait(timeout=5)
            self.process = None
            self.is_running = False
            return True, "Bot stopped successfully."
        except subprocess.TimeoutExpired:
            if os.name != 'nt':
                os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
            else:
                self.process.kill()
            self.process = None
            self.is_running = False
            return True, "Bot force stopped."
        except Exception as e:
            return False, f"Error stopping bot: {str(e)}"

    def restart(self):
        self.stop()
        return self.start()

    def _read_logs(self):
        while self.process and self.process.stdout:
            line = self.process.stdout.readline()
            if not line:
                break
            self.log_buffer.append(line.strip())
        self.is_running = False

    def get_status(self):
        status = "online" if self.is_running and self.process and self.process.poll() is None else "offline"
        return {
            "status": status,
            "pid": self.process.pid if self.process else None
        }

    def get_logs(self):
        return list(self.log_buffer)

    def clear_logs(self):
        self.log_buffer.clear()
