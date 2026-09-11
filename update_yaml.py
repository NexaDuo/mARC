import yaml
import sys

with open('.github/workflows/token-benchmark.yml') as f:
    data = f.read()

# I'll just use sed or python replace since it's simple
