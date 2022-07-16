# Copyright 2022 Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import argparse
import logging
import sys
import time
from threading import Event, Thread

import sdnotify
from lingua_franca import load_languages
from mycroft.configuration import Configuration
from mycroft_bus_client import Message, MessageBusClient

from .load import create_skill_instance, load_skill_source

SERVICE_ID = "skills"
LOG = logging.getLogger(SERVICE_ID)
NOTIFIER = sdnotify.SystemdNotifier()
WATCHDOG_DELAY = 0.5


def main():
    """Service entry point"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skill-directory", required=True, help="Path to skill directory"
    )
    parser.add_argument("--skill-id", required=True, help="Mycroft skill id")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[
            logging.FileHandler(f"/var/log/mycroft/{SERVICE_ID}.log", mode="a"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    LOG.info("Starting service...")

    try:
        bus = _connect_to_bus()
        config = Configuration.get()
        _load_language(config)

        skill_module = load_skill_source(args.skill_directory, args.skill_id)
        assert (
            skill_module is not None
        ), f"Failed to load skill module from {args.skill_directory}"

        skill_instance = create_skill_instance(skill_module, args.skill_id, bus)
        assert skill_instance is not None, f"Failed to create skill {args.skill_id}"

        # Start watchdog thread
        Thread(target=_watchdog, daemon=True).start()

        # Inform systemd that we successfully started
        NOTIFIER.notify("READY=1")

        try:
            # Wait for exit signal
            Event().wait()
        except KeyboardInterrupt:
            pass
        finally:
            skill_instance.default_shutdown()
            bus.close()

        LOG.info("Service is shutting down...")
    except Exception:
        LOG.exception("Service failed to start")


def _load_language(config):
    lang_code = config.get("lang", "en-us")
    load_languages([lang_code, "en-us"])


def _connect_to_bus() -> MessageBusClient:
    bus = MessageBusClient()
    bus.run_in_thread()
    bus.connected_event.wait()
    LOG.info("Connected to Mycroft Core message bus")

    return bus


def _watchdog():
    try:
        while True:
            # Prevent systemd from restarting service
            NOTIFIER.notify("WATCHDOG=1")
            time.sleep(WATCHDOG_DELAY)
    except Exception:
        LOG.exception("Unexpected error in watchdog thread")


if __name__ == "__main__":
    main()