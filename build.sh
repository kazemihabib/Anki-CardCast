#!/bin/bash

# Define variables
ADDON_DIR="cardcast"
OUTPUT_FILE="cardcast_release.ankiaddon"

# Remove old release if it exists
rm -f $OUTPUT_FILE

# Zip the contents of the cardcast directory
# Moving into the directory first ensures the zip doesn't include the parent folder structure
cd $ADDON_DIR
zip -r "../$OUTPUT_FILE" . -x "*.DS_Store" "*__pycache__*"

echo "Successfully built $OUTPUT_FILE"