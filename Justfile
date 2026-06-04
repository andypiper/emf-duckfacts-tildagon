# Default serial port/device for the badge.
# Can be overridden on the command line, e.g.: just device=/dev/ttyACM0 install
device := "/dev/ttyACM0"

# Path to the simulator directory of the badge-2024-software repo
sim_dir := "/home/andypiper/Development/third-party/badge-2024-software/sim"

# SDL video driver to use with the simulator
sdl_videodriver := "wayland"

# Target application directory path on the badge filesystem
badge_app_dir := ":/apps/andypiper_emf_duckfacts_tildagon"

# List available recipes
default:
    @just --list

# Run the app in the simulator
sim:
    cd {{sim_dir}} && SDL_VIDEODRIVER={{sdl_videodriver}} python run.py andypiper_emf_duckfacts_tildagon.DuckFactsApp

# Install all files (app code, metadata, and assets) to the badge
install:
    mpremote connect {{device}} cp assets/* {{badge_app_dir}}/assets/
    mpremote connect {{device}} cp app.py {{badge_app_dir}}/
    mpremote connect {{device}} reset

# Install only python source and metadata files to the badge
install-code:
    mpremote connect {{device}} cp app.py {{badge_app_dir}}/
    mpremote connect {{device}} reset

# Reset/reboot the badge
reset:
    mpremote connect {{device}} reset
