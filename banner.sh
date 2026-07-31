#!/bin/bash

echo -ne "\\033[2J\033[3;1f"
cat "$(dirname "$0")/assets/banner.txt"
printf "\n\n\033[1;32mLegacy newgen is running!\033[0m"
