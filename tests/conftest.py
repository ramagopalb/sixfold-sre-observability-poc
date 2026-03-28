# conftest.py — pytest configuration for Sixfold SRE POC tests
import sys
import os

# Add POC_Project root to sys.path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
