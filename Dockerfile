###########################################################
# setup default directories and configs
FROM automaticrippingmachine/arm-dependencies:1.8.0 AS base

LABEL org.opencontainers.image.source=https://github.com/pontiadic42/automatic-automatic-ripping-machine
LABEL org.opencontainers.image.license=MIT
LABEL org.opencontainers.image.description='A2RM: Automatic Ripping Machine fork for fully automated Blu-ray, DVD and audio disc ripping.'

EXPOSE 8080

# Setup folders and fstab
RUN \
    mkdir -m 0777 -p /home/arm \
    /home/arm/config /mnt/dev/sr0 \
    /mnt/dev/sr1 /mnt/dev/sr2 /mnt/dev/sr3 /mnt/dev/sr4 \
    /mnt/dev/sr5 /mnt/dev/sr6 /mnt/dev/sr7 /mnt/dev/sr8 \
    /mnt/dev/sr9 /mnt/dev/sr10 /mnt/dev/sr11 /mnt/dev/sr12 \
    /mnt/dev/sr13 /mnt/dev/sr14 /mnt/dev/sr15 /mnt/dev/sr16 \
    /mnt/dev/sr17 /mnt/dev/sr18 /mnt/dev/sr19 /mnt/dev/sr20 \
    && \
    echo "/dev/sr0  /mnt/dev/sr0  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr1  /mnt/dev/sr1  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr2  /mnt/dev/sr2  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr3  /mnt/dev/sr3  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr4  /mnt/dev/sr4  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr5  /mnt/dev/sr5  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr6  /mnt/dev/sr6  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr7  /mnt/dev/sr7  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr8  /mnt/dev/sr8  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr9  /mnt/dev/sr9  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr10  /mnt/dev/sr10  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr11  /mnt/dev/sr11  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr12  /mnt/dev/sr12  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr13  /mnt/dev/sr13  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr14  /mnt/dev/sr14  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr15  /mnt/dev/sr15  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr16  /mnt/dev/sr16  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr17  /mnt/dev/sr17  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr18  /mnt/dev/sr18  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr19  /mnt/dev/sr19  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab && \
    echo "/dev/sr20  /mnt/dev/sr20  udf,iso9660  defaults,users,utf8,ro  0  0" >> /etc/fstab


# Remove SSH
RUN rm -rf /etc/service/sshd /etc/my_init.d/00_regen_ssh_host_keys.sh

# Add ARMui service
RUN mkdir /etc/service/armui
COPY ./scripts/docker/runsv/armui.sh /etc/service/armui/run
RUN chmod +x /etc/service/armui/run

# Create our startup scripts
RUN mkdir -p /etc/my_init.d
COPY ./scripts/docker/runit/arm_user_files_setup.sh /etc/my_init.d/arm_user_files_setup.sh
COPY ./scripts/docker/runit/start_udev.sh /etc/my_init.d/start_udev.sh
RUN chmod +x /etc/my_init.d/*.sh

# We need to use a modified udev
COPY ./scripts/docker/custom_udev /etc/init.d/udev
RUN chmod +x /etc/my_init.d/*.sh

###########################################################
# A2RM: current MakeMKV, optional Intel QuickSync
#   MAKEMKV_VERSION   "latest" (looked up on makemkv.com) or a version such as 2.0.0.
#                     Docker caches this step, so to pick up a new release pass the version
#                     (scripts/update-makemkv.sh does) or build with --no-cache.
#   INTEL_QSV=1       Intel media driver + oneVPL runtime from Intel's repository, and HandBrake
#                     compiled against them (~10 min). See docs/quicksync.md.
#   HANDBRAKE_VERSION HandBrake version built when INTEL_QSV=1.
ARG MAKEMKV_VERSION=latest
ARG INTEL_QSV=0
ARG HANDBRAKE_VERSION=1.11.2
COPY ./scripts/docker/image/ /opt/a2rm-build/
RUN bash /opt/a2rm-build/makemkv.sh "$MAKEMKV_VERSION"
RUN if [ "$INTEL_QSV" = "1" ]; then bash /opt/a2rm-build/intel_qsv.sh "$HANDBRAKE_VERSION"; fi
# Tesseract for title-card OCR (TITLE_OCR); English only, ~10 MB
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
COPY ./scripts/docker/a2rm-check.sh /usr/local/bin/a2rm-check
RUN chmod +x /usr/local/bin/a2rm-check


###########################################################
# Final image pushed for use
FROM base AS automatic-ripping-machine

# Copy over source code
COPY . /opt/arm/

# Our docker udev rule
RUN ln -sv /opt/arm/setup/51-docker-arm.rules /lib/udev/rules.d/

# Allow git to be managed from the /opt/arm folders
RUN git config --global --add safe.directory /opt/arm

CMD ["/sbin/my_init"]
WORKDIR /home/arm
