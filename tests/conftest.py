import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "rootfs", "app")

sys.path.insert(0, os.path.abspath(APP))
sys.path.insert(0, os.path.join(HERE, "reference"))
sys.path.insert(0, HERE)
