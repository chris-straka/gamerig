# SPDX-FileCopyrightText: 2026 rigforge contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for tools/auto_rig.sh isolated overlay & Blender lookup."""

import os
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTO_RIG_SH = os.path.join(ROOT, "tools", "auto_rig.sh")


class AutoRigShTest(unittest.TestCase):
    def test_auto_rig_sh_uses_isolated_rf_scripts_dir_when_unset(self):
        with tempfile.TemporaryDirectory() as d:
            fake_blender = os.path.join(d, "blender")
            out_env = os.path.join(d, "env.txt")
            with open(fake_blender, "w") as f:
                f.write(
                    f'#!/bin/sh\necho "$BLENDER_USER_SCRIPTS" > "{out_env}"\nexit 0\n'
                )
            os.chmod(fake_blender, os.stat(fake_blender).st_mode | stat.S_IXUSR)

            env = {
                k: v
                for k, v in os.environ.items()
                if k not in ("RIGFORGE_SCRIPTS", "RF_SCRIPTS")
            }
            env["BLENDER_BIN"] = fake_blender
            r = subprocess.run(
                [AUTO_RIG_SH, "--help"], env=env, capture_output=True, text=True
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(out_env) as f:
                used = f.read().strip()
            self.assertIn("rf_scripts", used)
            self.assertNotEqual(
                used,
                "/tmp/rf_scripts",
                "auto_rig.sh must use a unique temp rf_scripts dir when unset to avoid concurrent rsync races",
            )
            self.assertFalse(
                os.path.exists(used),
                "auto-created temp rf_scripts dir should be cleaned up on exit",
            )

    def test_auto_rig_sh_propagates_blender_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as d:
            fake_blender = os.path.join(d, "blender")
            with open(fake_blender, "w") as f:
                f.write("#!/bin/sh\nexit 3\n")
            os.chmod(fake_blender, os.stat(fake_blender).st_mode | stat.S_IXUSR)
            env = dict(os.environ, BLENDER_BIN=fake_blender)
            r = subprocess.run(
                [AUTO_RIG_SH, "--help"], env=env, capture_output=True, text=True
            )
            self.assertEqual(r.returncode, 3)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
