def get_h265_nal_unit_type(packet_data: bytes) -> int | None:
    """
    Extract NAL unit type from H.265/HEVC packet data.
    For packets with multiple NAL units, prioritizes picture NAL types (0-21)
    over metadata types (32-40). Returns safe keyframes first (16-20), then
    other picture types, then metadata.

    H.265 NAL unit types:
    - 0-21: Picture NAL types (actual video data, priority over metadata)
    - 16-18: BLA frames (safe cut points)
    - 19, 20: IDR frames (safe cut points)
    - 21: CRA frame (not safe for cutting due to RASL pictures)
    - 32-34: VPS, SPS, PPS (parameter sets)
    - 35: AUD (Access Unit Delimiter)
    """
    if not packet_data or len(packet_data) < 6:
        return None

    data_len = len(packet_data)

    # H.265 in MP4 containers uses length-prefixed NAL units, not Annex B start codes
    # Try MP4/ISOBMFF format first (4-byte length prefix)
    # Read the first NAL unit length (big-endian 4 bytes)
    nal_length = int.from_bytes(packet_data[:4], byteorder='big')
    # Avoid misinterpreting Annex B start codes as MP4 lengths
    # Annex B start codes are 0x00000001 or 0x000001, which would be lengths 1 or very small
    if nal_length > 4 and nal_length <= data_len - 4:
        # Found valid length-prefixed NAL units - scan all of them
        nal_types_found = []
        i = 0
        while i < data_len - 4:
            nal_len = int.from_bytes(packet_data[i:i+4], byteorder='big')
            if nal_len < 2 or nal_len > data_len - i - 4:
                break  # Invalid NAL length
            if i + 5 < data_len:
                nal_type = (packet_data[i + 4] >> 1) & 0x3F
                nal_types_found.append(nal_type)
                # Found safe keyframe - prioritize these
                if nal_type in (16, 17, 18, 19, 20):  # BLA or IDR frames
                    return nal_type
            i += 4 + nal_len

        # No safe keyframes found, prioritize picture types (0-21) over metadata (32-40)
        if nal_types_found:
            # First check for CRA frames (21) - these are picture types but need special handling
            for nal_type in nal_types_found:
                if nal_type == 21:  # CRA frame
                    return nal_type
            # Then check for any other picture NAL types (0-15)
            for nal_type in nal_types_found:
                if 0 <= nal_type <= 15:  # Other picture types
                    return nal_type
            # Finally return first metadata type if no pictures found
            return nal_types_found[0]

    # Try Annex B format (start codes) - use bytes.find() for fast C-level search
    nal_types_found = []
    start_code_4 = b'\x00\x00\x00\x01'
    start_code_3 = b'\x00\x00\x01'
    pos = 0

    while pos < data_len - 5:  # H.265 needs 2 bytes for NAL header after start code
        # Search for 4-byte start code first
        idx4 = packet_data.find(start_code_4, pos)
        idx3 = packet_data.find(start_code_3, pos)

        # No more start codes found
        if idx4 == -1 and idx3 == -1:
            break

        # Use whichever comes first (prefer 4-byte if at same position)
        if idx4 != -1 and (idx3 == -1 or idx4 <= idx3):
            if idx4 + 6 <= data_len:
                nal_type = (packet_data[idx4 + 4] >> 1) & 0x3F
                nal_types_found.append(nal_type)
                if nal_type in (16, 17, 18, 19, 20):  # BLA or IDR frames
                    return nal_type
            pos = idx4 + 4
        else:
            # idx3 comes first and isn't part of a 4-byte sequence
            if idx3 + 5 <= data_len:
                nal_type = (packet_data[idx3 + 3] >> 1) & 0x3F
                nal_types_found.append(nal_type)
                if nal_type in (16, 17, 18, 19, 20):  # BLA or IDR frames
                    return nal_type
            pos = idx3 + 3

    # No safe keyframes found, prioritize picture types (0-21) over metadata (32-40)
    if nal_types_found:
        # First check for CRA frames (21) - these are picture types but need special handling
        for nal_type in nal_types_found:
            if nal_type == 21:  # CRA frame
                return nal_type

        # Then check for any other picture NAL types (0-15)
        for nal_type in nal_types_found:
            if 0 <= nal_type <= 15:  # Other picture types
                return nal_type

        # Finally return first metadata type if no pictures found
        return nal_types_found[0]

    return None


def is_safe_h264_keyframe_nal(nal_type: int | None) -> bool:
    """
    Check if an H.264 NAL type represents a safe keyframe for cutting.

    Args:
        nal_type: H.264 NAL unit type (int)

    Returns:
        bool: True if this NAL type is safe for cutting
    """
    if nal_type is None:
        return True # Can't know for sure
    # Accept IDR frames (5), SEI (6), and parameter sets (7,8) as cutting points
    return nal_type in [5, 6, 7, 8]


def is_safe_h265_keyframe_nal(nal_type: int | None) -> bool:
    """
    Check if an H.265 NAL type represents a safe keyframe for cutting.

    Args:
        nal_type: H.265 NAL unit type (int)

    Returns:
        bool: True if this NAL type is safe for cutting
    """
    if nal_type is None:
        return True  # Can't know for sure
    # Accept BLA(16,17,18), IDR(19,20), CRA(21) frames and parameter sets (32,33,34)
    return nal_type in [16, 17, 18, 19, 20, 21, 32, 33, 34]


def is_rasl_nal_type(nal_type: int | None) -> bool:
    """
    Check if NAL type is RASL (Random Access Skipped Leading).

    RASL pictures (types 8-9) reference frames from before the associated CRA point.
    When cutting at a CRA frame, RASL pictures become undecodable because their
    reference frames are missing. They must be recoded to be properly displayed.

    Args:
        nal_type: H.265 NAL unit type (int)

    Returns:
        bool: True if this is a RASL NAL type
    """
    if nal_type is None:
        return False
    return nal_type in [8, 9]  # RASL_N (8), RASL_R (9)


def is_radl_nal_type(nal_type: int | None) -> bool:
    """
    Check if NAL type is RADL (Random Access Decodable Leading).

    RADL pictures (types 6-7) are leading pictures that do NOT reference frames
    before the associated IRAP point. They can be decoded without priming from
    a previous GOP.

    Args:
        nal_type: H.265 NAL unit type (int)

    Returns:
        bool: True if this is a RADL NAL type
    """
    if nal_type is None:
        return False
    return nal_type in [6, 7]  # RADL_N (6), RADL_R (7)


def is_leading_picture_nal_type(nal_type: int | None) -> bool:
    """
    Check if NAL type is a leading picture (RASL or RADL).

    Leading pictures are displayed before the associated IRAP in presentation order
    but decoded after. When cutting at an IRAP that has RASL pictures, all leading
    pictures (both RASL and RADL) should be recoded together for simplicity,
    especially when they are interleaved in PTS order.

    Args:
        nal_type: H.265 NAL unit type (int)

    Returns:
        bool: True if this is a leading picture NAL type (RASL or RADL)
    """
    return is_rasl_nal_type(nal_type) or is_radl_nal_type(nal_type)


def get_h264_nal_unit_type(packet_data: bytes) -> int | None:
    """
    Extract NAL unit type from H.264/AVC packet data.
    For packets with multiple NAL units, prioritizes picture NAL types (1-5)
    over metadata types (6-9). Returns type 5 (IDR) if found, otherwise
    returns the most important picture type, or first metadata type if no pictures.

    H.264 NAL unit types:
    - 5: IDR frame (safe cut point)
    - 1-4: Non-IDR slices (picture data, priority over metadata)
    - 7, 8: SPS, PPS (parameter sets)
    - 9: AUD (Access Unit Delimiter)
    """
    if not packet_data or len(packet_data) < 5:
        return None

    data_len = len(packet_data)

    # H.264 in MP4 containers uses length-prefixed NAL units, not Annex B start codes
    # Try MP4/ISOBMFF format first (4-byte length prefix)
    # Read the first NAL unit length (big-endian 4 bytes)
    nal_length = int.from_bytes(packet_data[:4], byteorder='big')
    # Avoid misinterpreting Annex B start codes as MP4 lengths
    # Annex B start codes are 0x00000001 or 0x000001, which would be lengths 1 or very small
    if nal_length > 4 and nal_length <= data_len - 4:
        # Found valid length-prefixed NAL units - scan all of them
        nal_types_found = []
        i = 0
        while i < data_len - 4:
            nal_len = int.from_bytes(packet_data[i:i+4], byteorder='big')
            if nal_len < 1 or nal_len > data_len - i - 4:
                break  # Invalid NAL length
            if i + 4 < data_len:
                nal_type = packet_data[i + 4] & 0x1F
                nal_types_found.append(nal_type)
                # Found IDR frame - highest priority!
                if nal_type == 5:
                    return 5
            i += 4 + nal_len

        # No IDR found, prioritize picture types (1-4) over metadata types (6-9)
        if nal_types_found:
            for nal_type in nal_types_found:
                if 1 <= nal_type <= 4:  # Non-IDR picture types
                    return nal_type
            return nal_types_found[0]

    # Try Annex B format (start codes) - use bytes.find() for fast C-level search
    nal_types_found = []
    start_code_4 = b'\x00\x00\x00\x01'
    start_code_3 = b'\x00\x00\x01'
    pos = 0

    while pos < data_len - 4:  # H.264 needs 1 byte for NAL header after start code
        # Search for 4-byte start code first
        idx4 = packet_data.find(start_code_4, pos)
        idx3 = packet_data.find(start_code_3, pos)

        # No more start codes found
        if idx4 == -1 and idx3 == -1:
            break

        # Use whichever comes first (prefer 4-byte if at same position)
        if idx4 != -1 and (idx3 == -1 or idx4 <= idx3):
            if idx4 + 5 <= data_len:
                nal_type = packet_data[idx4 + 4] & 0x1F
                nal_types_found.append(nal_type)
                if nal_type == 5:  # Found IDR frame - highest priority!
                    return 5
            pos = idx4 + 4
        else:
            # idx3 comes first and isn't part of a 4-byte sequence
            if idx3 + 4 <= data_len:
                nal_type = packet_data[idx3 + 3] & 0x1F
                nal_types_found.append(nal_type)
                if nal_type == 5:  # Found IDR frame - highest priority!
                    return 5
            pos = idx3 + 3

    # No IDR found, prioritize picture types (1-4) over metadata types (6-9)
    if nal_types_found:
        for nal_type in nal_types_found:
            if 1 <= nal_type <= 4:  # Non-IDR picture types
                return nal_type
        return nal_types_found[0]

    return None
