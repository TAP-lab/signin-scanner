"""Network monitoring and recovery module for sign-in scanner.

This module provides:
- Regular connectivity checks to detect internet connection issues
- Automatic network service restart after multiple failures
- Integration with hardware feedback system to display network errors
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Callable, Optional

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())

# Configuration
CHECK_INTERVAL_SECONDS = 30  # How often to check connectivity
FAILURE_THRESHOLD = 3  # Number of consecutive failures before restart
RESTART_COOLDOWN_SECONDS = 300  # Wait 5 minutes between restart attempts

# Hosts to ping for connectivity check (try multiple for reliability)
PING_HOSTS = [
    "8.8.8.8",  # Google DNS
    "1.1.1.1",  # Cloudflare DNS
]


class NetworkMonitor:
    """Monitor network connectivity and attempt recovery on failures."""

    def __init__(
        self,
        check_interval: float = CHECK_INTERVAL_SECONDS,
        failure_threshold: int = FAILURE_THRESHOLD,
        restart_cooldown: float = RESTART_COOLDOWN_SECONDS,
        on_connection_lost: Optional[Callable[[], None]] = None,
        on_connection_restored: Optional[Callable[[], None]] = None,
    ):
        """Initialize network monitor.

        Args:
            check_interval: Seconds between connectivity checks
            failure_threshold: Number of failures before attempting restart
            restart_cooldown: Seconds to wait between restart attempts
            on_connection_lost: Callback when connection is lost
            on_connection_restored: Callback when connection is restored
        """
        self.check_interval = check_interval
        self.failure_threshold = failure_threshold
        self.restart_cooldown = restart_cooldown
        self.on_connection_lost = on_connection_lost
        self.on_connection_restored = on_connection_restored

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._consecutive_failures = 0
        self._last_restart_time = 0.0
        self._was_connected = True  # Assume connected at start

    def start(self) -> None:
        """Start network monitoring in background thread."""
        if self._running:
            LOG.warning("Network monitor already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        LOG.info(
            "Network monitor started (interval=%ds, threshold=%d)",
            self.check_interval,
            self.failure_threshold,
        )

    def stop(self) -> None:
        """Stop network monitoring."""
        if not self._running:
            return

        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        LOG.info("Network monitor stopped")

    def check_connectivity(self) -> bool:
        """Check if internet connection is available.

        Returns:
            True if connected, False otherwise
        """
        for host in PING_HOSTS:
            try:
                # Ping with 2 second timeout, single packet
                result = subprocess.run(
                    ["ping", "-c", "1", "-W", "2", host],
                    capture_output=True,
                    timeout=3,
                )
                if result.returncode == 0:
                    LOG.debug("Connectivity check passed (host=%s)", host)
                    return True
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                LOG.debug("Ping to %s failed: %s", host, exc)
                continue
            except Exception as exc:
                LOG.warning("Unexpected error pinging %s: %s", host, exc)
                continue

        LOG.warning("All connectivity checks failed")
        return False

    def restart_network_services(self) -> bool:
        """Attempt to restart network services.

        Returns:
            True if restart attempted, False if skipped (cooldown)
        """
        now = time.time()
        if now - self._last_restart_time < self.restart_cooldown:
            LOG.info(
                "Skipping network restart (cooldown: %.0fs remaining)",
                self.restart_cooldown - (now - self._last_restart_time),
            )
            return False

        LOG.warning("Attempting to restart network services")
        self._last_restart_time = now

        # Try multiple approaches to restart networking
        commands = [
            ["sudo", "systemctl", "restart", "networking"],
            ["sudo", "systemctl", "restart", "NetworkManager"],
            ["sudo", "systemctl", "restart", "dhcpcd"],
        ]

        success = False
        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    LOG.info("Successfully ran: %s", " ".join(cmd))
                    success = True
                else:
                    LOG.debug(
                        "Command failed: %s (exit=%d)",
                        " ".join(cmd),
                        result.returncode,
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                LOG.debug("Could not run %s: %s", cmd, exc)
                continue
            except Exception as exc:
                LOG.warning("Error running %s: %s", cmd, exc)
                continue

        if success:
            LOG.info("Network service restart attempted")
        else:
            LOG.warning("No network services could be restarted")

        return True

    def _monitor_loop(self) -> None:
        """Main monitoring loop (runs in background thread)."""
        while self._running:
            try:
                is_connected = self.check_connectivity()

                if is_connected:
                    # Connection is good
                    if not self._was_connected:
                        # Connection restored
                        LOG.info(
                            "Network connection restored after %d failures",
                            self._consecutive_failures,
                        )
                        if self.on_connection_restored:
                            self.on_connection_restored()
                        self._was_connected = True
                    self._consecutive_failures = 0
                else:
                    # Connection failed
                    self._consecutive_failures += 1
                    LOG.warning(
                        "Network check failed (%d/%d)",
                        self._consecutive_failures,
                        self.failure_threshold,
                    )

                    if self._was_connected:
                        # First failure - trigger connection lost callback
                        LOG.warning("Network connection lost")
                        if self.on_connection_lost:
                            self.on_connection_lost()
                        self._was_connected = False

                    if self._consecutive_failures >= self.failure_threshold:
                        # Threshold reached - attempt restart
                        LOG.error(
                            "Network failure threshold reached (%d failures)",
                            self._consecutive_failures,
                        )
                        self.restart_network_services()

            except Exception as exc:
                LOG.exception("Error in network monitor loop: %s", exc)

            # Wait before next check
            time.sleep(self.check_interval)

    def is_connected(self) -> bool:
        """Get current connection status.

        Returns:
            True if currently connected, False otherwise
        """
        return self._was_connected

    def get_failure_count(self) -> int:
        """Get number of consecutive failures.

        Returns:
            Number of consecutive connectivity check failures
        """
        return self._consecutive_failures
