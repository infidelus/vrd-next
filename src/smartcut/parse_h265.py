
"""
- ae(v): context-adaptive arithmetic entropy-coded syntax element. The parsing process for this descriptor is
         specified in clause 9.3.
- b(8):  byte having any pattern of bit string (8 bits). The parsing process
         for this descriptor is specified by the return value of the function
         read_bits( 8 ).
- f(n):  fixed-pattern bit string using n bits written (from left to right)
         with the left bit first. The parsing process for this descriptor is specified
         by the return value of the function read_bits( n ).
- se(v): signed integer 0-th order Exp-Golomb-coded syntax element with the left bit first. The parsing process
         for this descriptor is specified in clause 9.2.
- u(n):  unsigned integer using n bits. When n is "v" in the syntax table, the number of bits varies in a manner
         dependent on the value of other syntax elements. The parsing process for this descriptor is specified by the
         return value of the function read_bits( n ) interpreted as a binary representation of an unsigned integer with
         most significant bit written first.
- ue(v): unsigned integer 0-th order Exp-Golomb-coded syntax element with the left bit first. The parsing
         process for this descriptor is specified in clause 9.2.

"""

import sys
import math
from collections import defaultdict
from bitstring import BitArray, BitStream, pack
import av


class SliceType:
    B = 0
    P = 1
    I = 2


class PredictionMode:
    MODE_INTRA = 0
    MODE_INTER = 1
    MODE_SKIP = 2

class PartModes:
    PART_2Nx2N = 0
    PART_2NxN = 1
    PART_Nx2N = 2
    PART_NxN = 3
    PART_2NxnU = 4
    PART_2NxnD = 5
    PART_nLx2N = 6
    PART_nRx2N = 7

class NalUnitType:
    """
    Table 7-1 - NAL unit type codes and NAL unit type classes
    copypaste from source/Lib/TLibCommon/CommonDef.h
    """
    NAL_UNIT_CODED_SLICE_TRAIL_N = 0
    NAL_UNIT_CODED_SLICE_TRAIL_R = 1

    NAL_UNIT_CODED_SLICE_TSA_N = 2
    NAL_UNIT_CODED_SLICE_TSA_R = 3

    NAL_UNIT_CODED_SLICE_STSA_N = 4
    NAL_UNIT_CODED_SLICE_STSA_R = 5

    NAL_UNIT_CODED_SLICE_RADL_N = 6
    NAL_UNIT_CODED_SLICE_RADL_R = 7

    NAL_UNIT_CODED_SLICE_RASL_N = 8
    NAL_UNIT_CODED_SLICE_RASL_R = 9

    NAL_UNIT_RESERVED_VCL_N10 = 10
    NAL_UNIT_RESERVED_VCL_R11 = 11
    NAL_UNIT_RESERVED_VCL_N12 = 12
    NAL_UNIT_RESERVED_VCL_R13 = 13
    NAL_UNIT_RESERVED_VCL_N14 = 14
    NAL_UNIT_RESERVED_VCL_R15 = 15

    NAL_UNIT_CODED_SLICE_BLA_W_LP = 16
    NAL_UNIT_CODED_SLICE_BLA_W_RADL = 17
    NAL_UNIT_CODED_SLICE_BLA_N_LP = 18
    NAL_UNIT_CODED_SLICE_IDR_W_RADL = 19
    NAL_UNIT_CODED_SLICE_IDR_N_LP = 20
    NAL_UNIT_CODED_SLICE_CRA = 21
    NAL_UNIT_RESERVED_IRAP_VCL22 = 22
    NAL_UNIT_RESERVED_IRAP_VCL23 = 23

    NAL_UNIT_RESERVED_VCL24 = 24
    NAL_UNIT_RESERVED_VCL25 = 25
    NAL_UNIT_RESERVED_VCL26 = 26
    NAL_UNIT_RESERVED_VCL27 = 27
    NAL_UNIT_RESERVED_VCL28 = 28
    NAL_UNIT_RESERVED_VCL29 = 29
    NAL_UNIT_RESERVED_VCL30 = 30
    NAL_UNIT_RESERVED_VCL31 = 31

    NAL_UNIT_VPS = 32
    NAL_UNIT_SPS = 33
    NAL_UNIT_PPS = 34
    NAL_UNIT_ACCESS_UNIT_DELIMITER = 35
    NAL_UNIT_EOS = 36
    NAL_UNIT_EOB = 37
    NAL_UNIT_FILLER_DATA = 38
    NAL_UNIT_PREFIX_SEI = 39
    NAL_UNIT_SUFFIX_SEI = 40

    NAL_UNIT_RESERVED_NVCL41 = 41
    NAL_UNIT_RESERVED_NVCL42 = 42
    NAL_UNIT_RESERVED_NVCL43 = 43
    NAL_UNIT_RESERVED_NVCL44 = 44
    NAL_UNIT_RESERVED_NVCL45 = 45
    NAL_UNIT_RESERVED_NVCL46 = 46
    NAL_UNIT_RESERVED_NVCL47 = 47
    NAL_UNIT_UNSPECIFIED_48 = 48
    NAL_UNIT_UNSPECIFIED_49 = 49
    NAL_UNIT_UNSPECIFIED_50 = 50
    NAL_UNIT_UNSPECIFIED_51 = 51
    NAL_UNIT_UNSPECIFIED_52 = 52
    NAL_UNIT_UNSPECIFIED_53 = 53
    NAL_UNIT_UNSPECIFIED_54 = 54
    NAL_UNIT_UNSPECIFIED_55 = 55
    NAL_UNIT_UNSPECIFIED_56 = 56
    NAL_UNIT_UNSPECIFIED_57 = 57
    NAL_UNIT_UNSPECIFIED_58 = 58
    NAL_UNIT_UNSPECIFIED_59 = 59
    NAL_UNIT_UNSPECIFIED_60 = 60
    NAL_UNIT_UNSPECIFIED_61 = 61
    NAL_UNIT_UNSPECIFIED_62 = 62
    NAL_UNIT_UNSPECIFIED_63 = 63
    NAL_UNIT_INVALID = 64

nal_names = {
    0: "NAL_UNIT_CODED_SLICE_TRAIL_N",
    1: "NAL_UNIT_CODED_SLICE_TRAIL_R",

    2: "NAL_UNIT_CODED_SLICE_TSA_N",
    3: "NAL_UNIT_CODED_SLICE_TSA_R",

    4: "NAL_UNIT_CODED_SLICE_STSA_N",
    5: "NAL_UNIT_CODED_SLICE_STSA_R",

    6: "NAL_UNIT_CODED_SLICE_RADL_N",
    7: "NAL_UNIT_CODED_SLICE_RADL_R",

    8: "NAL_UNIT_CODED_SLICE_RASL_N",
    9: "NAL_UNIT_CODED_SLICE_RASL_R",

    10: "NAL_UNIT_RESERVED_VCL_N10",
    11: "NAL_UNIT_RESERVED_VCL_R11",
    12: "NAL_UNIT_RESERVED_VCL_N12",
    13: "NAL_UNIT_RESERVED_VCL_R13",
    14: "NAL_UNIT_RESERVED_VCL_N14",
    15: "NAL_UNIT_RESERVED_VCL_R15",

    16: "NAL_UNIT_CODED_SLICE_BLA_W_LP",
    17: "NAL_UNIT_CODED_SLICE_BLA_W_RADL",
    18: "NAL_UNIT_CODED_SLICE_BLA_N_LP",
    19: "NAL_UNIT_CODED_SLICE_IDR_W_RADL",
    20: "NAL_UNIT_CODED_SLICE_IDR_N_LP",
    21: "NAL_UNIT_CODED_SLICE_CRA",
    22: "NAL_UNIT_RESERVED_IRAP_VCL22",
    23: "NAL_UNIT_RESERVED_IRAP_VCL23",

    24: "NAL_UNIT_RESERVED_VCL24",
    25: "NAL_UNIT_RESERVED_VCL25",
    26: "NAL_UNIT_RESERVED_VCL26",
    27: "NAL_UNIT_RESERVED_VCL27",
    28: "NAL_UNIT_RESERVED_VCL28",
    29: "NAL_UNIT_RESERVED_VCL29",
    30: "NAL_UNIT_RESERVED_VCL30",
    31: "NAL_UNIT_RESERVED_VCL31",

    32: "NAL_UNIT_VPS",
    33: "NAL_UNIT_SPS",
    34: "NAL_UNIT_PPS",
    35: "NAL_UNIT_ACCESS_UNIT_DELIMITER",
    36: "NAL_UNIT_EOS",
    37: "NAL_UNIT_EOB",
    38: "NAL_UNIT_FILLER_DATA",
    39: "NAL_UNIT_PREFIX_SEI",
    40: "NAL_UNIT_SUFFIX_SEI",

    41: "NAL_UNIT_RESERVED_NVCL41",
    42: "NAL_UNIT_RESERVED_NVCL42",
    43: "NAL_UNIT_RESERVED_NVCL43",
    44: "NAL_UNIT_RESERVED_NVCL44",
    45: "NAL_UNIT_RESERVED_NVCL45",
    46: "NAL_UNIT_RESERVED_NVCL46",
    47: "NAL_UNIT_RESERVED_NVCL47",
    48: "NAL_UNIT_UNSPECIFIED_48",
    49: "NAL_UNIT_UNSPECIFIED_49",
    50: "NAL_UNIT_UNSPECIFIED_50",
    51: "NAL_UNIT_UNSPECIFIED_51",
    52: "NAL_UNIT_UNSPECIFIED_52",
    53: "NAL_UNIT_UNSPECIFIED_53",
    54: "NAL_UNIT_UNSPECIFIED_54",
    55: "NAL_UNIT_UNSPECIFIED_55",
    56: "NAL_UNIT_UNSPECIFIED_56",
    57: "NAL_UNIT_UNSPECIFIED_57",
    58: "NAL_UNIT_UNSPECIFIED_58",
    59: "NAL_UNIT_UNSPECIFIED_59",
    60: "NAL_UNIT_UNSPECIFIED_60",
    61: "NAL_UNIT_UNSPECIFIED_61",
    62: "NAL_UNIT_UNSPECIFIED_62",
    63: "NAL_UNIT_UNSPECIFIED_63",
    64: "NAL_UNIT_INVALID"
}

class slice_segment_header(object):
    def __init__(self, state, nal, s):
        """
        7.3.6.1 General slice segment header syntax
        """
        start = s.pos

        self.state = state
        self.nal = nal

        self.slice_temporal_mvp_enabled_flag = 0
        self.dependent_slice_segment_flag = 0
        self.slice_segment_address = None

        self.first_slice_segment_in_pic_flag = s.read('uint:1')

        if NalUnitType.NAL_UNIT_CODED_SLICE_BLA_W_LP <= nal.nal_unit_type <= NalUnitType.NAL_UNIT_RESERVED_IRAP_VCL23:
            self.no_output_of_prior_pics_flag = s.read('uint:1')

        self.slice_pic_parameter_set_id = s.read('ue')

        if not self.first_slice_segment_in_pic_flag:
            if state['pic'].dependent_slice_segments_enabled_flag:
                self.dependent_slice_segment_flag = s.read('uint:1')

            # TODO redundant with slice data function
            MinCbLog2SizeY = state['sps'].log2_min_luma_coding_block_size_minus3 + 3
            CtbLog2SizeY = MinCbLog2SizeY + state['sps'].log2_diff_max_min_luma_coding_block_size
            Log2MinCuChromaQpOffsetSize = CtbLog2SizeY - state['pic'].diff_cu_chroma_qp_offset_depth
            CtbSizeY = 1 << CtbLog2SizeY
            PicWidthInCtbsY = int(math.ceil(state['sps'].pic_width_in_luma_samples / float(CtbSizeY)))
            PicHeightInCtbsY = int(math.ceil(state['sps'].pic_height_in_luma_samples / float(CtbSizeY)))
            PicSizeInCtbsY = PicWidthInCtbsY * PicHeightInCtbsY

            self.slice_segment_address_length = int(math.ceil(math.log(PicSizeInCtbsY, 2)))
            self.slice_segment_address = s.read('uint:' + str(self.slice_segment_address_length))

        if not self.dependent_slice_segment_flag:
            self.slice_reserved_flag = [s.read('uint:1') for _ in range(state['pic'].num_extra_slice_header_bits)]
            self.slice_type = s.read('ue')
            if state['pic'].output_flag_present_flag:
                self.pic_output_flag = s.read('uint:1')
            else:
                self.pic_output_flag = None
            if state['sps'].separate_colour_plane_flag == 1:
                self.colour_plane_id = s.read('uint:2')
            else:
                self.colour_plane_id = None

            if nal.nal_unit_type != NalUnitType.NAL_UNIT_CODED_SLICE_IDR_W_RADL and nal.nal_unit_type != NalUnitType.NAL_UNIT_CODED_SLICE_IDR_N_LP:
                self.slice_pic_order_cnt_lsb = s.read('uint:' + str(state['sps'].log2_max_pic_order_cnt_lsb_minus4 + 4))
                self.short_term_ref_pic_set_sps_flag = s.read('uint:1')
                if not self.short_term_ref_pic_set_sps_flag:
                    self.st_ref_pic_set = st_ref_pic_set(state['sps'].num_short_term_ref_pic_sets, s)
                elif state['sps'].num_short_term_ref_pic_sets > 1:
                    self.short_term_ref_pic_set_idx_bits = int(math.ceil(math.log(state['sps'].num_short_term_ref_pic_sets, 2)))
                    self.short_term_ref_pic_set_idx = s.read('uint:' + str(self.short_term_ref_pic_set_idx_bits))

                if state['sps'].long_term_ref_pics_present_flag:
                    if state['sps'].num_long_term_ref_pics_sps > 0:
                        self.num_long_term_sps = s.read('ue')

                    self.num_long_term_pics = s.read('ue')

                    self.lt_idx_sps = [None] * (self.num_long_term_sps + self.num_long_term_pics)
                    self.poc_lsb_lt = [None] * (self.num_long_term_sps + self.num_long_term_pics)
                    self.used_by_curr_pic_lt_flag = [None] * (self.num_long_term_sps + self.num_long_term_pics)
                    self.delta_poc_msb_present_flag = [None] * (self.num_long_term_sps + self.num_long_term_pics)
                    self.delta_poc_msb_present_flag = [None] * (self.num_long_term_sps + self.num_long_term_pics)
                    self.delta_poc_msb_cycle_lt = [None] * (self.num_long_term_sps + self.num_long_term_pics)

                    for i in range(self.num_long_term_sps + self.num_long_term_pics):
                        if i < self.num_long_term_sps:
                            if state['sps'].num_long_term_ref_pics_sps > 1:
                                self.lt_idx_sps_bits = int(math.ceil(math.log(state['sps'].num_long_term_ref_pics_sps, 2)))
                                self.lt_idx_sps[i] = s.read('uint:' + str(self.lt_idx_sps_bits))
                        else:
                            self.poc_lsb_lt[i] = s.read('ue')
                            self.used_by_curr_pic_lt_flag[i] = s.read('uint:1')

                        self.delta_poc_msb_present_flag[i] = s.read('uint:1')
                        if self.delta_poc_msb_present_flag[i]:
                            self.delta_poc_msb_cycle_lt[i] = s.read('ue')

                if state['sps'].sps_temporal_mvp_enabled_flag:
                    self.slice_temporal_mvp_enabled_flag = s.read('uint:1')

            if state['sps'].sample_adaptive_offset_enabled_flag:
                self.slice_sao_luma_flag = s.read('uint:1')

                if state['sps'].ChromaArrayType != 0:
                    self.slice_sao_chroma_flag = s.read('uint:1')
            else:
                self.slice_sao_luma_flag = None
                self.slice_sao_chroma_flag = None

            if self.slice_type == SliceType.P or self.slice_type == SliceType.B:
                # Defaults
                self.num_ref_idx_l1_active_minus1 = state['pic'].num_ref_idx_l1_default_active_minus1
                self.num_ref_idx_l0_active_minus1 = state['pic'].num_ref_idx_l0_default_active_minus1

                self.num_ref_idx_active_override_flag = s.read('uint:1')

                if self.num_ref_idx_active_override_flag:
                    self.num_ref_idx_l0_active_minus1 = s.read('ue')
                    if self.slice_type == SliceType.B:
                        self.num_ref_idx_l1_active_minus1 = s.read('ue')

                if state['pic'].lists_modification_present_flag and NumPicTotalCurr > 1:
                    raise Exception('ref_pic_lists_modification( )')
                if self.slice_type == SliceType.B:
                    self.mvd_l1_zero_flag = s.read('uint:1')
                if state['pic'].cabac_init_present_flag:
                    self.cabac_init_flag = s.read('uint:1')

                if self.slice_temporal_mvp_enabled_flag:
                    if self.slice_type == SliceType.B:
                        self.collocated_from_l0_flag = s.read('uint:1')
                    else:
                        self.collocated_from_l0_flag = 1

                    if (self.collocated_from_l0_flag and self.num_ref_idx_l0_active_minus1 > 0) or \
                        (not self.collocated_from_l0_flag and self.num_ref_idx_l1_active_minus1 > 0):
                        self.collocated_ref_idx = s.read('ue')

                if (state['pic'].weighted_pred_flag and self.slice_type == SliceType.P) or \
                    (state['pic'].weighted_bipred_flag and self.slice_type == SliceType.B):
                    raise Exception('pred_weight_table( )')

                self.five_minus_max_num_merge_cand = s.read('ue')

        self.slice_qp_delta = s.read('se')
        if state['pic'].pps_slice_chroma_qp_offsets_present_flag:
            self.slice_cb_qp_offset = s.read('se')
            self.slice_cr_qp_offset = s.read('se')
        if state['pic'].pps_extension_present_flag and state['pic'].pps_slice_act_qp_offsets_present_flag:
            self.slice_act_y_qp_offset = s.read('se')
            self.slice_act_cb_qp_offset = s.read('se')
            self.slice_act_cr_qp_offset = s.read('se')

        if state['pic'].pps_range_extension_flag and state['pic'].chroma_qp_offset_list_enabled_flag:
            self.cu_chroma_qp_offset_enabled_flag = s.read('uint:1')
        else:
            self.cu_chroma_qp_offset_enabled_flag = None
        if state['pic'].deblocking_filter_control_present_flag and state['pic'].deblocking_filter_override_enabled_flag:
            self.deblocking_filter_override_flag = s.read('uint:1')
        else:
            self.deblocking_filter_override_flag = 0
        if self.deblocking_filter_override_flag:
            self.slice_deblocking_filter_disabled_flag = s.read('uint:1')
            if not self.slice_deblocking_filter_disabled_flag:
                self.slice_beta_offset_div2 = s.read('se')
                self.slice_tc_offset_div2 = s.read('se')

        if state['pic'].pps_loop_filter_across_slices_enabled_flag and \
                (self.slice_sao_luma_flag or self.slice_sao_chroma_flag or
                not self.slice_deblocking_filter_disabled_flag):
            self.slice_loop_filter_across_slices_enabled_flag = s.read('uint:1')

        if state['pic'].tiles_enabled_flag or state['pic'].entropy_coding_sync_enabled_flag:
            self.num_entry_point_offsets = s.read('ue')
            if self.num_entry_point_offsets > 0:
                self.offset_len_minus1 = s.read('ue')
                self.offset_len = self.offset_len_minus1 + 1
                self.entry_point_offset_minus1 = [s.read('uint:' + str(self.offset_len)) for _ in range(self.num_entry_point_offsets)]
                self.entry_point_offsets = [o+1 for o in self.entry_point_offset_minus1]
                self.first_byte = [sum(self.entry_point_offsets[:i]) for i in range(self.num_entry_point_offsets + 1)]
                self.last_byte = [self.first_byte[i] + (self.entry_point_offsets + [-9999999999])[i] for i in range(self.num_entry_point_offsets + 1)]
                self.byte_length = [self.last_byte[i] - self.first_byte[i] for i in range(self.num_entry_point_offsets + 1)]

        if state['pic'].slice_segment_header_extension_present_flag:
            self.slice_segment_header_extension_length = s.read('ue')
            self.slice_segment_header_extension_data_byte = [s.read('uint:8') for _ in range(self.slice_segment_header_extension_length)]

        byte_alignment(s)
        bits = s[start:s.pos]
        assert(self.bits == bits)

    def NumPicTotalCurr(self, state, header):
        NumPicTotalCurr = 0

        UsedByCurrPicLt = []
        for i in range(header.num_long_term_sps + header.num_long_term_pics):
            if i < header.num_long_term_sps:
                UsedByCurrPicLt[i] = state['sps'].used_by_curr_pic_lt_sps_flag[header.lt_idx_sps[i]]
            else:
                UsedByCurrPicLt[i] = header.used_by_curr_pic_lt_flag[i]

        CurrRpsIdx = header.short_term_ref_pic_set_idx \
            if header.short_term_ref_pic_set_sps_flag == 1 \
            else header.num_short_term_ref_pic_sets

        for i in range(NumNegativePics[CurrRpsIdx]):
            if UsedByCurrPicS0[CurrRpsIdx][i]:
                NumPicTotalCurr += 1
        for i in range(NumPositivePics[CurrRpsIdx]):
            if UsedByCurrPicS1[CurrRpsIdx][i]:
                NumPicTotalCurr += 1
        for i in range(header.num_long_term_sps + header.num_long_term_pics):
            if UsedByCurrPicLt[i]:
                NumPicTotalCurr += 1
        if state['sps'].curr_pic_as_ref_enabled_flag:
            NumPicTotalCurr += 1

        # (why not 7.4.7.2?)
        #NumPicTotalCurr = 0

        #for i in range(NumNegativePics[CurrRpsIdx]):
        #    if UsedByCurrPicS0[CurrRpsIdx][i]:
        #        NumPicTotalCurr += 1
        #for i in range(NumPositivePics[CurrRpsIdx]):
        #    if UsedByCurrPicS1[CurrRpsIdx][i]:
        #        NumPicTotalCurr += 1
        #for i in range(num_long_term_sps + num_long_term_pics):
        #    if UsedByCurrPicLt[i]:
        #        NumPicTotalCurr += 1

        #NumPicTotalCurr += NumActiveRefLayerPics

        return NumPicTotalCurr

    @property
    def bits(self):
        """
        7.3.6.1 General slice segment header syntax
        """
        s = BitArray()

        s.append(pack('uint:1', self.first_slice_segment_in_pic_flag))

        if NalUnitType.NAL_UNIT_CODED_SLICE_BLA_W_LP <= self.nal.nal_unit_type <= NalUnitType.NAL_UNIT_RESERVED_IRAP_VCL23:
            s.append(pack('uint:1', self.no_output_of_prior_pics_flag))

        s.append(pack('ue', self.slice_pic_parameter_set_id))

        if not self.first_slice_segment_in_pic_flag:
            if self.state['pic'].dependent_slice_segments_enabled_flag:
                s.append(pack('uint:1', self.dependent_slice_segment_flag))

            s.append(pack('uint:' + str(self.slice_segment_address_length), self.slice_segment_address))

        if not self.dependent_slice_segment_flag:
            s.append(pack('uint:' + str(self.state['pic'].num_extra_slice_header_bits), self.slice_reserved_flag))
            s.append(pack('ue', self.slice_type))

            if self.state['pic'].output_flag_present_flag:
                s.append(pack('uint:1', self.pic_output_flag))
            if self.state['sps'].separate_colour_plane_flag == 1:
                s.append(pack('uint:2', self.colour_plane_id))

            if self.nal.nal_unit_type != NalUnitType.NAL_UNIT_CODED_SLICE_IDR_W_RADL and self.nal.nal_unit_type != NalUnitType.NAL_UNIT_CODED_SLICE_IDR_N_LP:
                s.append(pack('uint:' + str(self.state['sps'].log2_max_pic_order_cnt_lsb_minus4 + 4), self.slice_pic_order_cnt_lsb))
                s.append(pack('uint:1', self.short_term_ref_pic_set_sps_flag))

                if self.short_term_ref_pic_set_sps_flag and self.state['sps'].num_short_term_ref_pic_sets > 1:
                    s.append(pack('uint:' + str(self.short_term_ref_pic_set_idx_bits), self.short_term_ref_pic_set_idx))

                if self.state['sps'].long_term_ref_pics_present_flag:
                    if self.state['sps'].num_long_term_ref_pics_sps > 0:
                        s.append(pack('ue', self.num_long_term_sps))

                    s.append(pack('ue', self.num_long_term_pics))

                    for i in range(self.num_long_term_sps + self.num_long_term_pics):
                        if i < self.num_long_term_sps:
                            if self.state['sps'].num_long_term_ref_pics_sps > 1:
                                s.append(pack('uint:' + str(self.lt_idx_sps_bits), self.lt_idx_sps[i]))
                        else:
                            s.append(pack('ue', self.poc_lsb_lt[i]))
                            s.append(pack('uint:1', self.used_by_curr_pic_lt_flag[i]))

                        s.append(pack('uint:1', self.delta_poc_msb_present_flag[i]))
                        if self.delta_poc_msb_present_flag[i]:
                            self.delta_poc_msb_cycle_lt[i] = s.read('ue')

                if self.state['sps'].sps_temporal_mvp_enabled_flag:
                    s.append(pack('uint:1', self.slice_temporal_mvp_enabled_flag))

            if self.state['sps'].sample_adaptive_offset_enabled_flag:
                s.append(pack('uint:1', self.slice_sao_luma_flag))

                if self.state['sps'].ChromaArrayType != 0:
                    s.append(pack('uint:1', self.slice_sao_chroma_flag))

            if self.slice_type == SliceType.P or self.slice_type == SliceType.B:
                s.append(pack('uint:1', self.num_ref_idx_active_override_flag))

                if self.num_ref_idx_active_override_flag:
                    s.append(pack('ue', self.num_ref_idx_l0_active_minus1))
                    if self.slice_type == SliceType.B:
                        s.append(pack('ue', self.num_ref_idx_l1_active_minus1))

                if self.state['pic'].lists_modification_present_flag and NumPicTotalCurr > 1:
                    raise Exception('ref_pic_lists_modification( )')
                if self.slice_type == SliceType.B:
                    s.append(pack('uint:1', self.mvd_l1_zero_flag))
                if self.state['pic'].cabac_init_present_flag:
                    s.append(pack('uint:1', self.cabac_init_flag))

                if self.slice_temporal_mvp_enabled_flag:
                    if self.slice_type == SliceType.B:
                        s.append(pack('uint:1', self.collocated_from_l0_flag))

                    if (self.collocated_from_l0_flag and self.num_ref_idx_l0_active_minus1 > 0) or \
                        (not self.collocated_from_l0_flag and self.num_ref_idx_l1_active_minus1 > 0):
                        s.append(pack('ue', self.collocated_ref_idx))

                if (self.state['pic'].weighted_pred_flag and self.slice_type == SliceType.P) or \
                    (self.state['pic'].weighted_bipred_flag and self.slice_type == SliceType.B):
                    raise Exception('pred_weight_table( )')

                s.append(pack('ue', self.five_minus_max_num_merge_cand))

        s.append(pack('se', self.slice_qp_delta))
        if self.state['pic'].pps_slice_chroma_qp_offsets_present_flag:
            s.append(pack('se', self.slice_cb_qp_offset))
            s.append(pack('se', self.slice_cr_qp_offset))
        if self.state['pic'].pps_extension_present_flag and self.state['pic'].pps_slice_act_qp_offsets_present_flag:
            s.append(pack('se', self.slice_act_y_qp_offset))
            s.append(pack('se', self.slice_act_cb_qp_offset))
            s.append(pack('se', self.slice_act_cr_qp_offset))

        if self.state['pic'].pps_range_extension_flag and self.state['pic'].chroma_qp_offset_list_enabled_flag:
            s.append(pack('uint:1', self.cu_chroma_qp_offset_enabled_flag))
        if self.state['pic'].deblocking_filter_control_present_flag and self.state['pic'].deblocking_filter_override_enabled_flag:
            s.append(pack('uint:1', self.deblocking_filter_override_flag))
        if self.deblocking_filter_override_flag:
            s.append(pack('uint:1', self.slice_deblocking_filter_disabled_flag))
            if not self.slice_deblocking_filter_disabled_flag:
                s.append(pack('se', self.slice_beta_offset_div2))
                s.append(pack('se', self.slice_tc_offset_div2))

        if self.state['pic'].pps_loop_filter_across_slices_enabled_flag and \
                (self.slice_sao_luma_flag or self.slice_sao_chroma_flag or
                not self.slice_deblocking_filter_disabled_flag):
            s.append(pack('uint:1', self.slice_loop_filter_across_slices_enabled_flag))

        if self.state['pic'].tiles_enabled_flag or self.state['pic'].entropy_coding_sync_enabled_flag:
            s.append(pack('ue', self.num_entry_point_offsets))
            if self.num_entry_point_offsets > 0:
                s.append(pack('ue', self.offset_len_minus1))
                for i in range(self.num_entry_point_offsets):
                    s.append(pack('uint:' + str(self.offset_len), self.entry_point_offset_minus1[i]))

        if self.state['pic'].slice_segment_header_extension_present_flag:
            s.append(pack('ue', self.slice_segment_header_extension_length))
            for i in range(self.slice_segment_header_extension_length):
                s.append(pack('uint:8', self.slice_segment_header_extension_data_byte[i]))

        #byte_alignment
        s.append('0b1')
        while len(s) % 8 != 0:
            s.append('0b0')

        return s

    def clone(self):
        return slice_segment_header(self.state, self.nal, BitStream(self.bits))

    def show(self):
        print('--- Header')
        for k, v in vars(self).items():
            print(k, v)
        print('---')
        return self

def byte_alignment(s):
    assert(s.read('uint:1') == 1)
    while s.pos % 8 != 0:
        assert (s.read('uint:1') == 0)


class slice_segment_data(object):
    def __init__(self, state, header, s):
        #TODO
        # f(5992) = 688
        # f(10272) = 1216

        z = len(s) - s.pos
        #if z == 10272:
        #    self.bits = s.read('bits:' + str(z))
        #else:
        #    z -= 488
        #    z /= 8
        #    #z = 172*4
        #    self.bits = s.read('bits:' + str(z))
        #z -= 40
        self.bits = s.read('bits:' + str(z))
        return

        CtbAddrInTs, CtbAddrInRs = 0, 0
        self.CtbAddrRsToTs = self.GenerateCtbAddrRsToTs(state)
        self.CtbAddrTsToRs = self.GenerateCtbAddrTsToRs(state, self.CtbAddrRsToTs)
        self.TileId = self.GenerateTileId(state, self.CtbAddrRsToTs)


        while True:
            coding_tree_unit(state, header, self, CtbAddrInRs, CtbAddrInTs, s)
            self.end_of_slice_segment_flag = s.read('ue')
            CtbAddrInTs += 1
            CtbAddrInRs = self.CtbAddrTsToRs[CtbAddrInTs]

            if not self.end_of_slice_segment_flag and \
                    ((state['pic'].tiles_enabled_flag and self.TileId[CtbAddrInTs] != self.TileId[CtbAddrInTs - 1]) or
                         (state['pic'].entropy_coding_sync_enabled_flag and
                              (CtbAddrInTs % self.PicWidthInCtbsY == 0 or
                                       self.TileId[CtbAddrInTs] != self.TileId[self.CtbAddrRsToTs[CtbAddrInRs - 1]]))):
                self.end_of_subset_one_bit = s.read('ue')
                assert(self.end_of_subset_one_bit == 1)
                byte_alignment(s)

            if self.end_of_slice_segment_flag:
                break

    def show(self):
        print('data ' + self.bits.hex)
        return self

    def GenerateTileId(self, state, CtbAddrRsToTs):
        TileId = [0] * (max(CtbAddrRsToTs) + 1)
        tileIdx = 0
        for j in range(state['pic'].num_tile_rows):
            for i in range(state['pic'].num_tile_columns):
                for y in range(self.rowBd[j], self.rowBd[j + 1]):
                    for x in range(self.colBd[i], self.colBd[i + 1]):
                        TileId[CtbAddrRsToTs[y * self.PicWidthInCtbsY + x]] = tileIdx
                tileIdx += 1
        return TileId

    def GenerateCtbAddrTsToRs(self, state, CtbAddrRsToTs):
        CtbAddrTsToRs = [0] * self.PicSizeInCtbsY
        for ctbAddrRs in range(self.PicSizeInCtbsY):
            CtbAddrTsToRs[CtbAddrRsToTs[ctbAddrRs]] = ctbAddrRs
        return CtbAddrTsToRs

    def GenerateCtbAddrRsToTs(self, state):
        self.MinCbLog2SizeY = state['sps'].log2_min_luma_coding_block_size_minus3 + 3
        self.CtbLog2SizeY = self.MinCbLog2SizeY + state['sps'].log2_diff_max_min_luma_coding_block_size
        self.Log2MinCuChromaQpOffsetSize = self.CtbLog2SizeY - state['pic'].diff_cu_chroma_qp_offset_depth
        CtbSizeY = 1 << self.CtbLog2SizeY
        self.PicWidthInCtbsY = int(math.ceil(state['sps'].pic_width_in_luma_samples / float(CtbSizeY)))
        PicHeightInCtbsY = int(math.ceil(state['sps'].pic_height_in_luma_samples / float(CtbSizeY)))
        self.PicSizeInCtbsY = self.PicWidthInCtbsY * PicHeightInCtbsY

        colWidth = [0] * state['pic'].num_tile_columns
        if state['pic'].uniform_spacing_flag:
            for i in range(state['pic'].num_tile_columns):
                colWidth[i] = ((i + 1) * self.PicWidthInCtbsY) / (state['pic'].num_tile_columns_minus1 + 1) - (i * self.PicWidthInCtbsY) / (state['pic'].num_tile_columns_minus1 + 1)
        else:
            colWidth[state['pic'].num_tile_columns_minus1] = self.PicWidthInCtbsY
            for i in range(state['pic'].num_tile_columns):
                colWidth[i] = state['pic'].column_width_minus1[i] + 1
                colWidth[state['pic'].num_tile_columns_minus1]  -=  colWidth[i]

        rowHeight = [0] * state['pic'].num_tile_rows
        if state['pic'].uniform_spacing_flag:
            for j in range(state['pic'].num_tile_rows):
                rowHeight[j] = ((j + 1) * PicHeightInCtbsY) / (state['pic'].num_tile_rows_minus1 + 1) - (j * PicHeightInCtbsY) / (state['pic'].num_tile_rows_minus1 + 1)
        else:
            rowHeight[state['pic'].num_tile_rows_minus1] = PicHeightInCtbsY
            for j in range(state['pic'].num_tile_rows):
                rowHeight[j] = state['pic'].row_height_minus1[j] + 1
                rowHeight[state['pic'].num_tile_rows_minus1]  -=  rowHeight[j]

        self.colBd = [0]
        for i in range(state['pic'].num_tile_columns):
            self.colBd.append(self.colBd[i] + colWidth[i])

        self.rowBd = [0]
        for j in range(state['pic'].num_tile_rows):
            self.rowBd.append(self.rowBd[j] + rowHeight[j])

        CtbAddrRsToTs = [0] * self.PicSizeInCtbsY
        for ctbAddrRs in range(self.PicSizeInCtbsY):
            tbX = ctbAddrRs % self.PicWidthInCtbsY
            tbY = ctbAddrRs / self.PicWidthInCtbsY

            for i in range(state['pic'].num_tile_columns):
                if tbX >= self.colBd[i]:
                    tileX = i
            for j in range(state['pic'].num_tile_rows):
                if tbY >= self.rowBd[j]:
                    tileY = j

            CtbAddrRsToTs[ctbAddrRs] = 0

            for i in range(tileX):
                CtbAddrRsToTs[ctbAddrRs] += rowHeight[tileY] * colWidth[i]
            for j in range(tileY):
                CtbAddrRsToTs[ctbAddrRs] += self.PicWidthInCtbsY * rowHeight[j]

            CtbAddrRsToTs[ctbAddrRs] += (tbY - self.rowBd[tileY]) * colWidth[tileX] + tbX - self.colBd[tileX]

        return CtbAddrRsToTs


class coding_tree_unit(object):
    def __init__(self, state, header, segment, CtbAddrInRs, CtbAddrInTs, s):
        xCtb = (CtbAddrInRs % segment.PicWidthInCtbsY) << segment.CtbLog2SizeY
        yCtb = (CtbAddrInRs / segment.PicWidthInCtbsY) << segment.CtbLog2SizeY

        #TODO
        SliceAddrRs = 0
        SaoTypeIdx = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: None)))
        sao_offset_abs = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: None)))
        sao_offset_sign = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: None)))
        sao_band_position = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: None)))
        split_cu_flag = defaultdict(lambda: defaultdict(lambda: None))

        if header.slice_sao_luma_flag or header.slice_sao_chroma_flag:
            self.sao(state, header, segment, CtbAddrInRs, CtbAddrInTs, SliceAddrRs, SaoTypeIdx, sao_offset_abs, sao_offset_sign, sao_band_position, xCtb >> segment.CtbLog2SizeY, yCtb >> segment.CtbLog2SizeY, s)
        self.coding_quadtree(state, header, segment, split_cu_flag, xCtb, yCtb, segment.CtbLog2SizeY, 0, s)

    def sao(self, state, header, segment, CtbAddrInRs, CtbAddrInTs, SliceAddrRs, SaoTypeIdx, sao_offset_abs, sao_offset_sign, sao_band_position, rx, ry, s):
        sao_merge_left_flag, sao_merge_up_flag = None, None
        sao_type_idx_luma = None

        ChromaArrayType = state['sps'].chroma_format_idc if state['sps'].separate_colour_plane_flag == 0 else 0

        if rx > 0:
            leftCtbInSliceSeg = CtbAddrInRs > SliceAddrRs
            leftCtbInTile = segment.TileId[CtbAddrInTs] == segment.TileId[segment.CtbAddrRsToTs[CtbAddrInRs - 1]]
            if leftCtbInSliceSeg and leftCtbInTile:
                sao_merge_left_flag = s.read('ue')

        if ry > 0 and not sao_merge_left_flag:
            upCtbInSliceSeg = (CtbAddrInRs - header.PicWidthInCtbsY) >= SliceAddrRs
            upCtbInTile = segment.TileId[CtbAddrInTs] == segment.TileId[segment.CtbAddrRsToTs[CtbAddrInRs - header.PicWidthInCtbsY]]
            if upCtbInSliceSeg and upCtbInTile:
                sao_merge_up_flag = s.read('ue')

        if not sao_merge_up_flag and not sao_merge_left_flag:
            for cIdx in range(3 if ChromaArrayType != 0 else 1):
                if (header.slice_sao_luma_flag and cIdx == 0) or \
                   (header.slice_sao_chroma_flag and cIdx > 0):
                    if cIdx == 0:
                        sao_type_idx_luma = s.read('ue')
                        sao_type_idx_chroma = None
                    elif cIdx == 1:
                        sao_type_idx_chroma = s.read('ue')
                        sao_type_idx_luma = None
                    else:
                        sao_type_idx_chroma = None
                        sao_type_idx_luma = None

                    # See 7.4.9.3
                    if ChromaArrayType == 0:
                        if sao_type_idx_luma:
                            SaoTypeIdx[0][rx][ry] = sao_type_idx_luma
                        elif sao_merge_left_flag == 1:
                            SaoTypeIdx[0][rx][ry] = SaoTypeIdx[0][rx - 1][ry]
                        elif sao_merge_up_flag == 1:
                            SaoTypeIdx[0][rx][ry] = SaoTypeIdx[0][rx][ry - 1]
                        else:
                            SaoTypeIdx[0][rx][ry] = 0
                    else:
                        if sao_type_idx_chroma:
                            SaoTypeIdx[cIdx][rx][ry] = sao_type_idx_chroma
                        elif sao_merge_left_flag == 1:
                            SaoTypeIdx[cIdx][rx][ry] = SaoTypeIdx[cIdx][rx - 1][ry]
                        elif sao_merge_up_flag == 1:
                            SaoTypeIdx[cIdx][rx][ry] = SaoTypeIdx[cIdx][rx][ry - 1 ]
                        else:
                            SaoTypeIdx[cIdx][rx][ry] = 0

                    """
                    # See 7.4.9.3
                    if sao_offset_abs[cIdx][rx][ry][i] is not None:
                        if sao_merge_left_flag == 1:
                            sao_offset_abs[cIdx][rx][ry][i] = sao_offset_abs[cIdx][rx - 1][ry][i]
                        elif sao_merge_up_flag == 1:
                            sao_offset_abs[cIdx][rx][ry][i] = sao_offset_abs[cIdx][rx][ry - 1][i]
                        else:
                            sao_offset_abs[cIdx][rx][ry][i] = 0

                    # See 7.4.9.3
                    if sao_offset_sign[cIdx][rx][ry][i] is None:
                        if sao_merge_left_flag == 1:
                            sao_offset_sign[cIdx][rx][ry][i] = sao_offset_sign[cIdx][rx - 1][ry][i]
                        elif sao_merge_up_flag == 1:
                            sao_offset_sign[cIdx][rx][ry][i] = sao_offset_sign[cIdx][rx][ry - 1][i]
                        elif SaoTypeIdx[cIdx][rx][ry] == 2:
                            if i in [0, 1]:
                                sao_offset_sign[cIdx][rx][ry][i] = 0
                            else: # (i == 2 or 3)
                                sao_offset_sign[cIdx][rx][ry][i] = 1
                        else:
                            sao_offset_sign[cIdx][rx][ry][i] = 0

                    if sao_band_position[cIdx][rx][ry] is None:
                        if sao_merge_left_flag == 1:
                            sao_band_position[cIdx][rx][ry] = sao_band_position[cIdx][rx - 1][ry]
                        elif sao_merge_up_flag == 1:
                            sao_band_position[cIdx][rx][ry] = sao_band_position[cIdx][rx][ry - 1]
                        else:
                            sao_band_position[cIdx][rx][ry] = 0
                    """

                    if SaoTypeIdx[cIdx][rx][ry] != 0:
                        for i in range(4):
                            sao_offset_abs[cIdx][rx][ry][i] = s.read('ue')
                        if SaoTypeIdx[cIdx][rx][ry] == 1:
                            for i in range(4):
                                if sao_offset_abs[cIdx][rx][ry][i] != 0:
                                    sao_offset_sign[cIdx][rx][ry][i] = s.read('ue')
                            sao_band_position[cIdx][rx][ry] = s.read('ue')
                        else:
                            if cIdx == 0:
                                sao_eo_class_luma = s.read('ue')
                            if cIdx == 1:
                                sao_eo_class_chroma = s.read('ue')

    def coding_quadtree(self, state, header, segment, split_cu_flag, x0, y0, log2CbSize, cqtDepth, s):
        if x0 + (1 << log2CbSize) <= state['sps'].pic_width_in_luma_samples and \
            y0 + (1 << log2CbSize) <= state['sps'].pic_height_in_luma_samples and \
                        log2CbSize > segment.MinCbLog2SizeY:
                split_cu_flag[x0][y0] = s.read('ue')

        if state['pic'].cu_qp_delta_enabled_flag and log2CbSize >= header.Log2MinCuQpDeltaSize:
            IsCuQpDeltaCoded = 0
            CuQpDeltaVal = 0

        if header.cu_chroma_qp_offset_enabled_flag and \
            log2CbSize >= segment.Log2MinCuChromaQpOffsetSize:
            IsCuChromaQpOffsetCoded = 0

        if split_cu_flag[x0][y0]:
            x1 = x0 + (1 << (log2CbSize - 1))
            y1 = y0 + (1 << (log2CbSize - 1))
            self.coding_quadtree(state, header, segment, split_cu_flag, x0, y0, log2CbSize - 1, cqtDepth + 1, s)

            if x1 < state['sps'].pic_width_in_luma_samples:
                self.coding_quadtree(state, header, segment, split_cu_flag, x1, y0, log2CbSize - 1, cqtDepth + 1, s)
            if y1 < state['sps'].pic_height_in_luma_samples:
                self.coding_quadtree(state, header, segment, split_cu_flag, x0, y1, log2CbSize - 1, cqtDepth + 1, s)
            if x1 < state['sps'].pic_width_in_luma_samples and y1 < state['sps'].pic_height_in_luma_samples:
                self.coding_quadtree(state, header, segment, split_cu_flag, x1, y1, log2CbSize - 1, cqtDepth + 1, s)
        else:
            self.coding_unit(state, header, segment, x0, y0, log2CbSize, s)

    def coding_unit(self, state, header, segment, x0, y0, log2CbSize, s):
        #TODO
        cu_skip_flag = defaultdict(lambda: defaultdict(lambda: None))
        palette_mode_flag = defaultdict(lambda: defaultdict(lambda: None))
        CuPredMode = defaultdict(lambda: defaultdict(lambda: None))
        pcm_flag = defaultdict(lambda: defaultdict(lambda: 0))
        prev_intra_luma_pred_flag = defaultdict(lambda: defaultdict(lambda: None))
        mpm_idx = defaultdict(lambda: defaultdict(lambda: None))
        rem_intra_luma_pred_mode = defaultdict(lambda: defaultdict(lambda: None))
        intra_chroma_pred_mode = defaultdict(lambda: defaultdict(lambda: None))

        if state['pic'].transquant_bypass_enabled_flag:
            cu_transquant_bypass_flag = s.read('ue')
        if header.slice_type != SliceType.I:
            cu_skip_flag[x0][y0] = s.read('ue')

        nCbS = (1 << log2CbSize)
        if cu_skip_flag[x0][y0]:
            self.prediction_unit(x0, y0, nCbS, nCbS)
        else:
            if header.slice_type != SliceType.I:
                pred_mode_flag = s.read('ue')
            else:
                pred_mode_flag = None

            if pred_mode_flag == 0:
                CuPredMode[x0][y0] = PredictionMode.MODE_INTER
            elif pred_mode_flag == 1:
                CuPredMode[x0][y0] = PredictionMode.MODE_INTRA
            else:
                assert(pred_mode_flag is None)
                if header.slice_type == SliceType.I:
                    CuPredMode[x0][y0] = PredictionMode.MODE_INTRA
                else:
                    if cu_skip_flag[x0][y0] == 1:
                        CuPredMode[x0][y0] = PredictionMode.MODE_SKIP
                    else:
                        CuPredMode[x0][y0] = None

            if state['sps'].palette_mode_enabled_flag and CuPredMode[x0][y0] == PredictionMode.MODE_INTRA and log2CbSize <= MaxTbLog2SizeY:
                palette_mode_flag[x0][y0] = s.read('ue')
            if palette_mode_flag[x0][y0]:
                palette_coding(x0, y0, nCbS)
            else:
                if CuPredMode[x0][y0] != PredictionMode.MODE_INTRA or log2CbSize == segment.MinCbLog2SizeY:
                    part_mode = s.read('ue')
                else:
                    part_mode = None

                if CuPredMode[x0][y0] == PredictionMode.MODE_INTRA:
                    PartMode = PartModes.PART_2Nx2N if part_mode == 0 else PartModes.PART_NxN
                    IntraSplitFlag = part_mode
                elif CuPredMode[x0][y0] == PredictionMode.MODE_INTER:
                    PartMode = [PartModes.PART_2Nx2N, PartModes.PART_2NxN, PartModes.PART_Nx2N, PartModes.PART_NxN, PartModes.PART_2NxnU, PartModes.PART_2NxnD, PartModes.PART_nLx2N, PartModes.PART_nRx2N][part_mode]
                    IntraSplitFlag = 0
                else:
                    raise Exception('Check spec')

                if CuPredMode[x0][y0] == PredictionMode.MODE_INTRA:
                    if PartMode == PartModes.PART_2Nx2N and pcm_enabled_flag and \
                                    log2CbSize >= Log2MinIpcmCbSizeY and \
                                    log2CbSize <= Log2MaxIpcmCbSizeY:
                        pcm_flag[x0][y0] = s.read('ue')

                    if pcm_flag[x0][y0]:
                        while not byte_aligned():
                            pcm_alignment_zero_bit = s.read('uint:1') # f(1)
                        pcm_sample(x0, y0, log2CbSize)
                    else:
                        pbOffset = (nCbS / 2) if PartMode == PartModes.PART_NxN else nCbS
                        for j in range(0, nCbS, pbOffset):
                            for i in range(0, nCbS, pbOffset):
                                prev_intra_luma_pred_flag[x0 + i][y0 + j] = s.read('ue')
                        for j in range(0, nCbS, pbOffset):
                            for i in range(0, nCbS, pbOffset):
                                if prev_intra_luma_pred_flag[x0 + i][y0 + j]:
                                    mpm_idx[x0 + i][y0 + j] = s.read('ue')
                                else:
                                    rem_intra_luma_pred_mode[x0 + i][y0 + j] = s.read('ue')
                        if state['sps'].ChromaArrayType == 3:
                            for j in range(0, nCbS, pbOffset):
                                for i in range(0, nCbS, pbOffset):
                                    intra_chroma_pred_mode[x0 + i][y0 + j] = s.read('ue')
                        elif state['sps'].ChromaArrayType != 0:
                            intra_chroma_pred_mode[x0][y0] = s.read('ue')
                else:
                    if PartMode == PartModes.PART_2Nx2N:
                        self.prediction_unit(x0, y0, nCbS, nCbS)
                    elif PartMode == PartModes.PART_2NxN:
                        self.prediction_unit(x0, y0, nCbS, nCbS / 2)
                        self.prediction_unit(x0, y0 + (nCbS / 2), nCbS, nCbS / 2)
                    elif PartMode == PartModes.PART_Nx2N:
                        self.prediction_unit(x0, y0, nCbS / 2, nCbS)
                        self.prediction_unit(x0 + (nCbS / 2), y0, nCbS / 2, nCbS)
                    elif PartMode == PartModes.PART_2NxnU:
                        self.prediction_unit(x0, y0, nCbS, nCbS / 4)
                        self.prediction_unit(x0, y0 + (nCbS / 4), nCbS, nCbS * 3 / 4)
                    elif PartMode == PartModes.PART_2NxnD:
                        self.prediction_unit(x0, y0, nCbS, nCbS * 3 / 4)
                        self.prediction_unit(x0, y0 + (nCbS * 3 / 4), nCbS, nCbS / 4)
                    elif PartMode == PartModes.PART_nLx2N:
                        self.prediction_unit(x0, y0, nCbS / 4, nCbS)
                        self.prediction_unit(x0 + (nCbS / 4), y0, nCbS * 3 / 4, nCbS)
                    elif PartMode == PartModes.PART_nRx2N:
                        self.prediction_unit(x0, y0, nCbS * 3 / 4, nCbS)
                        self.prediction_unit(x0 + (nCbS * 3 / 4), y0, nCbS / 4, nCbS)
                    else: # PART_NxN
                        self.prediction_unit(x0, y0, nCbS / 2, nCbS / 2)
                        self.prediction_unit(x0 + (nCbS / 2), y0, nCbS / 2, nCbS / 2)
                        self.prediction_unit(x0, y0 + (nCbS / 2), nCbS / 2, nCbS / 2)
                        self.prediction_unit(x0 + (nCbS / 2), y0 + (nCbS / 2), nCbS / 2, nCbS / 2)

                if not pcm_flag[x0][y0]:
                    if CuPredMode[x0][y0] != PredictionMode.MODE_INTRA and \
                        not (PartMode == PartModes.PART_2Nx2N and merge_flag[x0][y0]):
                        rqt_root_cbf = s.read('ue')
                    else:
                        rqt_root_cbf = None

                    if rqt_root_cbf:
                        if (residual_adaptive_colour_transform_enabled_flag and
                            (CuPredMode[x0][y0] == PredictionMode.MODE_INTER or
                            (PartMode == PART_2Nx2N and
                            intra_chroma_pred_mode[x0][y0] == 4) or
                            (intra_chroma_pred_mode[x0][y0] == 4  and
                            intra_chroma_pred_mode[x0 + nCbS/2][y0] == 4  and
                            intra_chroma_pred_mode[x0][y0 + nCbS/2] == 4  and
                            intra_chroma_pred_mode[x0 + nCbS/2][y0 + nCbS/2] == 4))):
                            cu_residual_act_flag = s.read('ue')

                        MaxTrafoDepth = (max_transform_hierarchy_depth_intra + IntraSplitFlag) \
                            if CuPredMode[x0][y0] == PredictionMode.MODE_INTRA \
                            else max_transform_hierarchy_depth_inter
                        self.transform_tree(x0, y0, x0, y0, log2CbSize, 0, 0)

    def prediction_unit(self, x0, y0, asdf, qwer):
        print('PU')
        exit(1)

    def transform_tree(self, a1, a2, a3, a4, a5, a6, a7):
        print('TT')
        exit(1)

class rbsp_slice_segment_trailing_bits(object):
    def __init__(self, s):
        """
        """
        pass
    def show(self):
        return self

class hrd_parameters(object):
    def __init__(self, s, commonInfPresentFlag, maxNumSubLayersMinus1):
        """
        E.2.2 HRD parameters syntax
        """
        self.t='\t\t'
        if commonInfPresentFlag:
            self.nal_hrd_parameters_present_flag = s.read('uint:1')
            self.vcl_hrd_parameters_present_flag = s.read('uint:1')
            if self.nal_hrd_parameters_present_flag or self.vcl_hrd_parameters_present_flag:
                self.sub_pic_hrd_params_present_flag = s.read('uint:1')
                if self.sub_pic_hrd_params_present_flag:
                    self.tick_divisor_minus2 = s.read('uint:8')
                    self.du_cpb_removal_delay_increment_length_minus1 = s.read('uint:5')
                    self.sub_pic_cpb_params_in_pic_timing_sei_flag = s.read('uint:1')
                    self.dpb_output_delay_du_length_minus1 = s.read('uint:5')
                self.bit_rate_scale = s.read('uint:4')
                self.cpb_size_scale = s.read('uint:4')
                if self.sub_pic_hrd_params_present_flag:
                    self.icpb_size_du_scale = s.read('uint:4')
                self.initial_cpb_removal_delay_length_minus1 = s.read('uint:5')
                self.au_cpb_removal_delay_length_minus1 = s.read('uint:5')
                self.dpb_output_delay_length_minus1 = s.read('uint:5')
        for i in range(maxNumSubLayersMinus1 + 1):
            self.fixed_pic_rate_general_flag[i] = s.read('uint:1')
            if not self.fixed_pic_rate_general_flag[i]:
                self.fixed_pic_rate_within_cvs_flag[i] = s.read('uint:1')
            if self.fixed_pic_rate_within_cvs_flag[i]:
                self.elemental_duration_in_tc_minus1[i] = s.read('ue')
            else:
                self.low_delay_hrd_flag[i] = s.read('uint:1')
            if not self.low_delay_hrd_flag[i]:
                self.cpb_cnt_minus1[i] = s.read('ue')
            if self.nal_hrd_parameters_present_flag:
                sub_layer_hrd_parameters(s, i)
            if self.vcl_hrd_parameters_present_flag:
                self.sub_layer_hrd_parameters(s,i)

    def show(self):
        """
        """
        attrs = vars(self)
        print(self.t, 'hrd parameters')
        print(self.t, '==============')
        for k, v in attrs.items():
            print(k, v)
        return self


class slice_segment_layer_rbsp(object):
    def __init__(self, state, nal, s):
        """
        Interpret next bits in BitString s as an ...
        7.3.2.9 Slice segment layer RBSP syntax
        """
        self.t = '\t'
        self.state = state
        self.nal = nal
        self.header = slice_segment_header(state, nal, s).show()
        self.body = slice_segment_data(state, self.header, s).show()
        rbsp_slice_segment_trailing_bits(s)

    @property
    def bits(self):
        return BitArray(self.header.bits + self.body.bits)

    def clone(self):
        return slice_segment_layer_rbsp(self.state, self.nal, BitStream(self.bits))

    def show(self):
        return self

class video_parameter_set_rbsp(object):
    def __init__(self, s):
        """
        Interpret next bits in BitString s as an VPS
        7.3.2.1 Video parameter set RBSP syntax
        """
        self.t = '\t'
        self.vps_video_parameter_set_id = s.read('uint:4')
        self.vps_reserved_three_2bits = s.read('uint:2')
        self.vps_max_layers_minus1 = s.read('uint:6')
        self.vps_max_sub_layers_minus1 = s.read('uint:3')
        self.vps_temporal_id_nesting_flag = s.read('uint:1')
        self.vps_reserved_0xffff_16bits = s.read('uint:16')

        self.ptl = profile_tier_level(s, self.vps_max_sub_layers_minus1)

        self.vps_sub_layer_ordering_info_present_flag = s.read('uint:1')
        i = 0 if self.vps_sub_layer_ordering_info_present_flag else self.vps_max_sub_layers_minus1
        self.vps_max_dec_pic_buffering_minus1 = []
        self.vps_max_num_reorder_pics = []
        self.vps_max_latency_increase_plus1 = []
        for n in range(self.vps_max_sub_layers_minus1 + 1):
            self.vps_max_dec_pic_buffering_minus1.append(s.read('ue'))
            self.vps_max_num_reorder_pics.append(s.read('ue'))
            self.vps_max_latency_increase_plus1.append(s.read('ue'))
        self.vps_max_layer_id = s.read('uint:1')
        self.vps_num_layer_sets_minus1 = s.read('uint:1')
        for i in range(self.vps_num_layer_sets_minus1 + 1):
            for j in range(self.vps_max_layer_id + 1):
                #layer_id_included_flag[ i ][ j ]
                s.read('uint:1')


        self.vps_timing_info_present_flag = s.read('uint:1')
        if self.vps_timing_info_present_flag:
            self.vps_num_units_in_tick = s.read('uint:1')
            self.vps_time_scale = s.read('uint:1')
            self.vps_poc_proportional_to_timing_flag = s.read('uint:1')
            if self.vps_poc_proportional_to_timing_flag:
                self.vps_num_ticks_poc_diff_one_minus1 = s.read('uint:1')
            self.vps_num_hrd_parameters = s.read('uint:1')
            self.hrd_layer_set_idx = []
            self.cprms_present_flag = []
            for i in range(self.vps_num_hrd_parameters):
                self.hrd_layer_set_idx.append(s.read('ue'))
                if i > 0:
                    cprms_present_flag.append(s.read('uint:1'))
                    self.hrdp = hrd_parameters(cprms_present_flag[i], self.vps_max_sub_layers_minus1)
        self.vps_extension_flag = s.read('uint:1')
#        if self.vps_extension_flag:
#        while( more_rbsp_data( ) )
#        vps_extension_data_flag
#        rbsp_trailing_bits( )

    def show(self):
        print()
        print(self.t, 'Video parameter Set RBSP')
        print(self.t, '========================')
        print(self.t, 'vps_video_parameter_set_id', self.vps_video_parameter_set_id)
        print(self.t, 'vps_reserved_three_2bits', self.vps_reserved_three_2bits)
        print(self.t, 'vps_max_layers_minus1', self.vps_max_layers_minus1)
        print(self.t, 'vps_max_sub_layers_minus1', self.vps_max_sub_layers_minus1)
        print(self.t, 'vps_temporal_id_nesting_flag', self.vps_temporal_id_nesting_flag)
        print(self.t, 'vps_reserved_0xffff_16bits', self.vps_reserved_0xffff_16bits)

        self.ptl.show()

        print()
        print(self.t, 'vps_sub_layer_ordering_info_present_flag', self.vps_sub_layer_ordering_info_present_flag)
        for n in range(self.vps_max_sub_layers_minus1 + 1):
            print(self.t, 'vps_max_dec_pic_buffering_minus1', self.vps_max_dec_pic_buffering_minus1)
            print(self.t, 'vps_max_num_reorder_pics', self.vps_max_num_reorder_pics)
            print(self.t, 'vps_max_latency_increase_plus1', self.vps_max_latency_increase_plus1)
        print(self.t, 'vps_max_layer_id', self.vps_max_layer_id)
        print(self.t, 'vps_num_layer_sets_minus1', self.vps_num_layer_sets_minus1)
        for i in range(self.vps_num_layer_sets_minus1 + 1):
            for j in range(self.vps_max_layer_id + 1):
                #layer_id_included_flag[ i ][ j ]
                pass

        print(self.t, 'vps_timing_info_present_flag', self.vps_timing_info_present_flag)
        if self.vps_timing_info_present_flag:
            print(self.t, 'vps_num_units_in_tick', self.vps_num_units_in_tick)
            print(self.t, 'vps_time_scale', self.vps_time_scale)
            print(self.t, 'vps_poc_proportional_to_timing_flag', self.vps_poc_proportional_to_timing_flag)
            if self.vps_poc_proportional_to_timing_flag:
                print(self.t, 'vps_num_ticks_poc_diff_one_minus1', self.vps_num_ticks_poc_diff_one_minus1)
            print(self.t, 'vps_num_hrd_parameters', self.vps_num_hrd_parameters)
            for i in range(self.vps_num_hrd_parameters):
                self.hrd_layer_set_idx.append(s.read('ue'))
                if i > 0:
                    cprms_present_flag.append(s.read('uint:1'))
                    self.hrdp.show()
        print(self.t, 'vps_extension_flag', self.vps_extension_flag)
        return self

class profile_tier_level(object):
    def __init__(self, s, maxNumSubLayersMinus1):
        """
        Interpret next bits in BitString s as an profile_tier_level
        7.3.3 Profile, tier and level syntax
        """
        self.t = '\t\t'
        self.general_profile_space = s.read('uint:2')
        self.general_tier_flag = s.read('uint:1')
        self.general_profile_idc = s.read('uint:5')
        self.general_profile_compatibility_flag = [s.read('uint:1') for _ in range(32)]
        self.general_progressive_source_flag = s.read('uint:1')
        self.general_interlaced_source_flag = s.read('uint:1')
        self.general_non_packed_constraint_flag = s.read('uint:1')
        self.general_frame_only_constraint_flag = s.read('uint:1')
        self.general_reserved_zero_43bits = s.read('uint:43') #BH s.read('uint:44')
        self.general_inbld_flag = s.read('uint:1')
        self.general_level_idc = s.read('uint:8')

        self.sub_layer_profile_present_flag = []
        self.sub_layer_level_present_flag = []
        for _ in range(maxNumSubLayersMinus1):
            self.sub_layer_profile_present_flag.append(s.read('uint:1'))
            self.sub_layer_level_present_flag.append(s.read('uint:1'))

        self.reserved_zero_2bits = []
        if maxNumSubLayersMinus1 > 0:
            for _ in range(maxNumSubLayersMinus1, 8):
                self.reserved_zero_2bits.append(s.read('uint:2'))

        for i in range(maxNumSubLayersMinus1):
            if self.sub_layer_level_present_flag[i]:
                raise Exception('sub_layer_profile_space[ i ]')
            if self.sub_layer_level_present_flag[i]:
                raise Exception('sub_layer_level_idc[ i ]')

    def show(self):
        print()
        print(self.t, 'Profile Tier Level')
        print(self.t, '==================')
        print(self.t, 'general_profile_space', self.general_profile_space)
        print(self.t, 'general_tier_flag', self.general_tier_flag)
        print(self.t, 'general_profile_idc', self.general_profile_idc)
        for i in range(32):
            print("{}{}[{:2d}] {}".format(self.t, 'general_profile_compatibility_flag', i, self.general_profile_compatibility_flag[i]))
        print(self.t, 'general_progressive_source_flag', self.general_progressive_source_flag)
        print(self.t, 'general_interlaced_source_flag', self.general_interlaced_source_flag)
        print(self.t, 'general_non_packed_constraint_flag', self.general_non_packed_constraint_flag)
        print(self.t, 'general_frame_only_constraint_flag', self.general_frame_only_constraint_flag)
        print(self.t, 'general_reserved_zero_43bits', self.general_reserved_zero_43bits)
#        print(self.t, "{0:b}".format(self.general_reserved_zero_44bits))
        print(self.t, 'general_level_idc', self.general_level_idc)
        print(self.t, 'sub_layer_profile_present_flag', self.sub_layer_profile_present_flag)
        print(self.t, 'sub_layer_level_present_flag', self.sub_layer_level_present_flag)
        return self

class seq_parameter_set_rbsp(object):
    def __init__(self, s):
        """
        Interpret next bits in BitString s as an SPS
        7.3.2.2 Sequence parameter set RBSP syntax
        """
        self.t = '\t'
        self.sps_video_parameter_set_id = s.read('uint:4')
        # if nuh_layer_id == 0:
        self.sps_max_sub_layers_minus1 = s.read('uint:3') #s.read('uint:1')
        # else:
        #    self.sps_ext_or_max_sub_layers_minus1 = s.read('uint:3')
        self.sps_temporal_id_nesting_flag = s.read('uint:1')

        self.ptl = profile_tier_level(s, self.sps_max_sub_layers_minus1)

        ### BH

        self.sps_seq_parameter_set_id = s.read('ue')
        self.chroma_format_idc = s.read('ue')
        self.separate_colour_plane_flag = s.read('uint:1') if self.chroma_format_idc == 3 else 0

        self.ChromaArrayType = self.chroma_format_idc if self.separate_colour_plane_flag == 0 else 0

        self.pic_width_in_luma_samples = s.read('ue')
        self.pic_height_in_luma_samples = s.read('ue')
        self.conformance_window_flag = s.read('uint:1')

        if self.conformance_window_flag:
            self.conf_win_left_offset = s.read('ue')
            self.conf_win_right_offset = s.read('ue')
            self.conf_win_top_offset = s.read('ue')
            self.conf_win_bottom_offset = s.read('ue')

        self.bit_depth_luma_minus8 = s.read('ue')
        self.bit_depth_chroma_minus8 = s.read('ue')
        self.log2_max_pic_order_cnt_lsb_minus4 = s.read('ue')
        self.sps_sub_layer_ordering_info_present_flag = s.read('uint:1')

        self.sps_max_dec_pic_buffering_minus1 = []
        self.sps_max_num_reorder_pics = []
        self.sps_max_latency_increase_plus1 = []
        for _ in range(0 if self.sps_sub_layer_ordering_info_present_flag else self.sps_max_sub_layers_minus1, self.sps_max_sub_layers_minus1 + 1):
            self.sps_max_dec_pic_buffering_minus1.append(s.read('ue'))
            self.sps_max_num_reorder_pics.append(s.read('ue'))
            self.sps_max_latency_increase_plus1.append(s.read('ue'))

        self.log2_min_luma_coding_block_size_minus3 = s.read('ue')
        self.log2_diff_max_min_luma_coding_block_size = s.read('ue')
        self.log2_min_luma_transform_block_size_minus2 = s.read('ue')
        self.log2_diff_max_min_luma_transform_block_size = s.read('ue')
        self.max_transform_hierarchy_depth_inter = s.read('ue')
        self.max_transform_hierarchy_depth_intra = s.read('ue')
        self.scaling_list_enabled_flag = s.read('uint:1')

        if self.scaling_list_enabled_flag:
            self.sps_scaling_list_data_present_flag = s.read('uint:1')
            if self.sps_scaling_list_data_present_flag:
                raise Exception('scaling_list_data()')

        self.amp_enabled_flag = s.read('uint:1')
        self.sample_adaptive_offset_enabled_flag = s.read('uint:1')
        self.pcm_enabled_flag = s.read('uint:1')

        if self.pcm_enabled_flag:
            self.pcm_sample_bit_depth_luma_minus1 = s.read('uint:4')
            self.pcm_sample_bit_depth_chroma_minus1 = s.read('uint:4')
            self.log2_min_pcm_luma_coding_block_size_minus3 = s.read('ue')
            self.log2_diff_max_min_pcm_luma_coding_block_size = s.read('ue')
            self.pcm_loop_filter_disabled_flag = s.read('uint:1')

        self.num_short_term_ref_pic_sets = s.read('ue')
        self.short_term_ref_pic_sets = []
        for i in range(self.num_short_term_ref_pic_sets):
            self.short_term_ref_pic_sets.append(st_ref_pic_set(self.short_term_ref_pic_sets, i, self.num_short_term_ref_pic_sets, s))

        self.long_term_ref_pics_present_flag = s.read('uint:1')
        if self.long_term_ref_pics_present_flag:
            self.num_long_term_ref_pics_sps = s.read('ue')

            self.lt_ref_pic_poc_lsb_sps = []
            self.used_by_curr_pic_lt_sps_flag = []
            for _ in range(self.num_long_term_ref_pics_sps):
                bits = self.log2_max_pic_order_cnt_lsb_minus4 + 4
                self.lt_ref_pic_poc_lsb_sps.append(s.read('uint:' + str(bits)))
                self.used_by_curr_pic_lt_sps_flag.append(s.read('uint:1'))

        self.sps_temporal_mvp_enabled_flag = s.read('uint:1')
        self.strong_intra_smoothing_enabled_flag = s.read('uint:1')
        self.vui_parameters_present_flag = s.read('uint:1')

        if self.vui_parameters_present_flag:
            self.vui_parameters = vui_parameters(s)

        self.sps_extension_present_flag = s.read('uint:1')
        self.palette_mode_enabled_flag = None
        if self.sps_extension_present_flag:
            self.sps_range_extension_flag = s.read('uint:1')
            self.sps_multilayer_extension_flag = s.read('uint:1')
            self.sps_scc_extension_flag = s.read('uint:1')
            self.sps_extension_5bits = s.read('uint:5')

            if self.sps_range_extension_flag:
                raise Exception('sps_range_extension( )')
            if self.sps_multilayer_extension_flag:
                raise Exception('sps_multilayer_extension( )')
            if self.sps_scc_extension_flag:
                raise Exception('sps_scc_extension( )')
            else:
                self.curr_pic_as_ref_enabled_flag = None

            if self.sps_extension_5bits:
                raise Exception('while( more_rbsp_data( ) )\n sps_extension_data_flag u(1)')

        # TODO rbsp_trailing_bits( )

    def show(self):
        print()
        print(self.t, 'Sequence Parameter Set RBSP')
        print(self.t, '===========================')
        print(self.t, 'sps_video_parameter_set_id', self.sps_video_parameter_set_id)
        print(self.t, 'sps_max_sub_layers_minus1', self.sps_max_sub_layers_minus1)
        print(self.t, 'sps_temporal_id_nesting_flag', self.sps_temporal_id_nesting_flag)
        print(self.t, 'short_term_ref_pic_sets:')

        self.ptl.show()

        for sts in self.short_term_ref_pic_sets:
            sts.show()

        return self


class vui_parameters(object):
    def __init__(self, s):
        self.aspect_ratio_info_present_flag = s.read('uint:1')
        if self.aspect_ratio_info_present_flag:
            self.aspect_ratio_idc = s.read('uint:8')
            if self.aspect_ratio_idc == EXTENDED_SAR:
                self.sar_width = s.read('uint:16')
                self.sar_height = s.read('uint:16')

        self.overscan_info_present_flag = s.read('uint:1')
        if self.overscan_info_present_flag:
            self.overscan_appropriate_flag = s.read('uint:1')

        self.video_signal_type_present_flag = s.read('uint:1')
        if self.video_signal_type_present_flag:
            self.video_format = s.read('uint:3')
            self.video_full_range_flag = s.read('uint:1')
            self.colour_description_present_flag = s.read('uint:1')
            if self.colour_description_present_flag:
                self.colour_primaries = s.read('uint:8')
                self.transfer_characteristics = s.read('uint:8')
                self.matrix_coeffs = s.read('uint:8')

        self.chroma_loc_info_present_flag = s.read('uint:1')
        if self.chroma_loc_info_present_flag:
            self.chroma_sample_loc_type_top_field = s.read('ue')
            self.chroma_sample_loc_type_bottom_field = s.read('ue')

        self.neutral_chroma_indication_flag = s.read('uint:1')
        self.field_seq_flag = s.read('uint:1')
        self.frame_field_info_present_flag = s.read('uint:1')
        self.default_display_window_flag = s.read('uint:1')

        if self.default_display_window_flag:
            self.def_disp_win_left_offset = s.read('ue')
            self.def_disp_win_right_offset = s.read('ue')
            self.def_disp_win_top_offset = s.read('ue')
            self.def_disp_win_bottom_offset = s.read('ue')

        self.vui_timing_info_present_flag = s.read('uint:1')
        if self.vui_timing_info_present_flag:
            self.vui_num_units_in_tick = s.read('uint:32')
            self.vui_time_scale = s.read('uint:32')
            self.vui_poc_proportional_to_timing_flag = s.read('uint:1')

            if self.vui_poc_proportional_to_timing_flag:
                self.vui_num_ticks_poc_diff_one_minus1 = s.read('ue')

            self.vui_hrd_parameters_present_flag = s.read('uint:1')
            if self.vui_hrd_parameters_present_flag:
                raise Exception('hrd_parameters( 1, sps_max_sub_layers_minus1 )')

        self.bitstream_restriction_flag = s.read('uint:1')
        if self.bitstream_restriction_flag:
            self.tiles_fixed_structure_flag = s.read('uint:1')
            self.motion_vectors_over_pic_boundaries_flag = s.read('uint:1')
            self.restricted_ref_pic_lists_flag = s.read('uint:1')
            self.min_spatial_segmentation_idc = s.read('ue')
            self.max_bytes_per_pic_denom = s.read('ue')
            self.max_bits_per_min_cu_denom = s.read('ue')
            self.log2_max_mv_length_horizontal = s.read('ue')
            self.log2_max_mv_length_vertical = s.read('ue')

    def show(self):
        print('VUI parameters')


class st_ref_pic_set(object):
    def __init__(self, refs, stRpsIdx, num_short_term_ref_pic_sets, s):
        self.stRpsIdx = stRpsIdx
        self.inter_ref_pic_set_prediction_flag = s.read('uint:1') if stRpsIdx != 0 else None

        print('============ begin ' + str(stRpsIdx))
        print('inter_ref_pic_set_prediction_flag = ' + str(self.inter_ref_pic_set_prediction_flag))

        if self.inter_ref_pic_set_prediction_flag:
            if stRpsIdx == num_short_term_ref_pic_sets:
                self.delta_idx_minus1 = s.read('ue')
            else:
                self.delta_idx_minus1 = 0
            self.delta_idx = self.delta_idx_minus1 + 1

            self.delta_rps_sign = s.read('uint:1')
            self.abs_delta_rps_minus1 = s.read('ue')


            RefRpsIdx = stRpsIdx - self.delta_idx
            print('RefRpsIdx = %d' % RefRpsIdx)
            #NumDeltaPocs_RefRpsIdx = refs[RefRpsIdx].NumDeltaPocs
            deltaRps = (1 - 2 * self.delta_rps_sign) * (self.abs_delta_rps_minus1 + 1)

            self.used_by_curr_pic_flag = []
            self.use_delta_flag = []
            for j in range(refs[RefRpsIdx].NumDeltaPocs + 1):
                self.used_by_curr_pic_flag.append(s.read('uint:1'))
                if not self.used_by_curr_pic_flag[-1]:
                    self.use_delta_flag.append(s.read('uint:1'))
                else:
                    self.use_delta_flag.append(1)

            self.InitNumPics(refs, stRpsIdx, RefRpsIdx, deltaRps)

        else:
            self.num_negative_pics = s.read('ue')
            self.num_positive_pics = s.read('ue')
            self.NumDeltaPocs = self.num_positive_pics + self.num_negative_pics
            self.NumPositivePics = self.num_positive_pics
            self.NumNegativePics = self.num_negative_pics

            lastPocS = 0
            self.delta_poc_s0_minus1 = []
            self.used_by_curr_pic_s0_flag = []
            self.UsedByCurrPicS0 = []
            self.DeltaPocS0 = []
            for _ in range(self.num_negative_pics):
                self.delta_poc_s0_minus1.append(s.read('ue'))
                delta_poc_s0 = self.delta_poc_s0_minus1[-1] + 1
                self.used_by_curr_pic_s0_flag.append(s.read('uint:1'))

                self.DeltaPocS0.append(lastPocS - delta_poc_s0)
                self.UsedByCurrPicS0.append(self.used_by_curr_pic_s0_flag[-1])
                lastPocS = self.DeltaPocS0[-1]

            lastPocS = 0
            self.delta_poc_s1_minus1 = []
            self.used_by_curr_pic_s1_flag = []
            self.DeltaPocS1 = []
            self.UsedByCurrPicS1 = []
            for _ in range(self.num_positive_pics):
                self.delta_poc_s1_minus1.append(s.read('ue'))
                delta_poc_s1 = self.delta_poc_s1_minus1[-1] + 1
                self.used_by_curr_pic_s1_flag.append(s.read('uint:1'))

                self.DeltaPocS1.append(lastPocS + delta_poc_s1)
                self.UsedByCurrPicS1.append(self.used_by_curr_pic_s1_flag[-1])
                lastPocS = self.DeltaPocS1[-1]

    def InitNumPics(self, refs, stRpsIdx, RefRpsIdx, deltaRps):
        self.DeltaPocS0 = defaultdict(int)
        self.DeltaPocS1 = defaultdict(int)
        self.UsedByCurrPicS0 = defaultdict(lambda: None)
        self.UsedByCurrPicS1 = defaultdict(lambda: None)

        i = 0
        print('--- start positive=' + str(refs[RefRpsIdx].NumPositivePics) + ", range=" + str(range(refs[RefRpsIdx].NumPositivePics - 1, -1, -1)))
        for j in range(refs[RefRpsIdx].NumPositivePics - 1, -1, -1):
            print('j=' + str(j))
            dPoc = refs[RefRpsIdx].DeltaPocS1[j] + deltaRps
            if dPoc < 0 and self.use_delta_flag[refs[RefRpsIdx].NumNegativePics + j]:
                self.DeltaPocS0[i] = dPoc
                self.UsedByCurrPicS0[i] = self.used_by_curr_pic_flag[refs[RefRpsIdx].NumNegativePics + j]
                i += 1

        print('---')
        print(RefRpsIdx)
        print(refs[RefRpsIdx].NumDeltaPocs)
        print(self.use_delta_flag)
        print(i)
        if deltaRps < 0 and self.use_delta_flag[refs[RefRpsIdx].NumDeltaPocs]:
        # if deltaRps < 0 and refs[refs[RefRpsIdx].NumDeltaPocs].use_delta_flag:
            self.DeltaPocS0[i] = deltaRps
            self.UsedByCurrPicS0[i] = self.used_by_curr_pic_flag[refs[RefRpsIdx].NumDeltaPocs]
            i += 1

        for j in range(refs[RefRpsIdx].NumNegativePics):
            dPoc = refs[RefRpsIdx].DeltaPocS0[j] + deltaRps
            if dPoc < 0 and self.use_delta_flag[j]:
                self.DeltaPocS0[i] = dPoc
                self.UsedByCurrPicS0[i] = self.used_by_curr_pic_flag[j]
                i += 1

        self.NumNegativePics = i

        i = 0
        for j in range(refs[RefRpsIdx].NumNegativePics - 1, -1, -1):
            dPoc = refs[RefRpsIdx].DeltaPocS0[j] + deltaRps
            if dPoc > 0 and self.use_delta_flag[j]:
                self.DeltaPocS1[i] = dPoc
                self.UsedByCurrPicS1[i] = self.used_by_curr_pic_flag[j]
                i += 1

        if deltaRps > 0 and self.use_delta_flag[refs[RefRpsIdx].NumDeltaPocs]:
            self.DeltaPocS1[i] = deltaRps
            self.UsedByCurrPicS1[i] = self.used_by_curr_pic_flag[refs[RefRpsIdx].NumDeltaPocs]
            i += 1

        for j in range(refs[RefRpsIdx].NumPositivePics):
            dPoc = refs[RefRpsIdx].DeltaPocS1[j] + deltaRps
            if dPoc > 0 and self.use_delta_flag[refs[RefRpsIdx].NumNegativePics + j]:
                self.DeltaPocS1[i] = dPoc
                self.UsedByCurrPicS1[i] = self.used_by_curr_pic_flag[refs[RefRpsIdx].NumNegativePics + j]
                i += 1

        self.NumPositivePics = i
        self.NumDeltaPocs = self.NumNegativePics + self.NumPositivePics

        print('NumNegativePics = %d' % self.NumNegativePics)
        print('NumPositivePics = %d' % self.NumPositivePics)
        print('NumDeltaPocs = %d' % self.NumDeltaPocs)

    def show(self):
        self.t = '\t\t'
        print()
        print(self.t, 'Short Term Reference Picture Set(%d)' % self.stRpsIdx)
        print(self.t, '================================')
        print(self.t, 'inter_ref_pic_set_prediction_flag', self.inter_ref_pic_set_prediction_flag)
        if not self.inter_ref_pic_set_prediction_flag:
            print(self.t, 'num_negative_pics ', self.num_negative_pics)
            print(self.t, 'num_positive_pics', self.num_positive_pics)
            for i, (delta, used) in enumerate(zip(self.delta_poc_s0_minus1, self.used_by_curr_pic_s0_flag)):
                print(self.t, 'delta_poc_s0_minus1[%d]' % i, delta)
                print(self.t, 'used_by_curr_pic_s0_flag[%d]' % i, used)

            for i, val in enumerate(self.delta_poc_s1_minus1):
                print(self.t, 'delta_poc_s1_minus1[%d]' % i, val)
        else:
            print(self.t, 'delta_rps_sign', self.delta_rps_sign)
            print(self.t, 'abs_delta_rps_minus1', self.abs_delta_rps_minus1)
            for i, (pic, delta) in enumerate(zip(self.used_by_curr_pic_flag, self.use_delta_flag)):
                print(self.t, 'used_by_curr_pic_flag[%d]' % i, pic)
                if not pic:
                    print(self.t, 'use_delta_flag[%d]' % i, delta)


class pic_parameter_set_rbsp(object):
    def __init__(self, s):
        """
        Interpret next bits in BitString s as an PPS
        7.3.2.3 Picture parameter set RBSP syntax
        """
        self.t='\t'
        self.pps_pic_parameter_set_id = s.read('ue')
        self.pps_seq_parameter_set_id = s.read('ue')
        self.dependent_slice_segments_enabled_flag = s.read('uint:1')
        self.output_flag_present_flag = s.read('uint:1')
        self.num_extra_slice_header_bits = s.read('uint:3')
        self.sign_data_hiding_enabled_flag = s.read('uint:1')
        self.cabac_init_present_flag = s.read('uint:1')
        self.num_ref_idx_l0_default_active_minus1 = s.read('ue')
        self.num_ref_idx_l1_default_active_minus1 = s.read('ue')
        self.init_qp_minus26 = s.read('se')
        self.constrained_intra_pred_flag = s.read('uint:1')
        self.transform_skip_enabled_flag = s.read('uint:1')
        self.cu_qp_delta_enabled_flag = s.read('uint:1')
        if self.cu_qp_delta_enabled_flag:
            self.diff_cu_qp_delta_depth = s.read('ue')
        self.pps_cb_qp_offset = s.read('se')
        self.pps_cr_qp_offset = s.read('se')
        self.pps_slice_chroma_qp_offsets_present_flag = s.read('uint:1')
        self.weighted_pred_flag = s.read('uint:1')
        self.weighted_bipred_flag = s.read('uint:1')
        self.transquant_bypass_enabled_flag = s.read('uint:1')
        self.tiles_enabled_flag = s.read('uint:1')
        self.entropy_coding_sync_enabled_flag = s.read('uint:1')

        if self.tiles_enabled_flag:
            self.num_tile_columns_minus1 = s.read('ue')
            self.num_tile_columns = self.num_tile_columns_minus1 + 1
            self.num_tile_rows_minus1 = s.read('ue')
            self.num_tile_rows = self.num_tile_rows_minus1 + 1
            self.uniform_spacing_flag = s.read('uint:1')
            if not self.uniform_spacing_flag:
                self.column_width_minus1 = [s.read('ue') for _ in range(self.num_tile_columns_minus1)]
                self.row_height_minus1 = [s.read('ue') for _ in range(self.num_tile_rows_minus1)]
            self.loop_filter_across_tiles_enabled_flagi = s.read('uint:1')
        self.pps_loop_filter_across_slices_enabled_flag = s.read('uint:1')
        self.deblocking_filter_control_present_flag = s.read('uint:1')
        if self.deblocking_filter_control_present_flag:
            self.deblocking_filter_override_enabled_flag = s.read('uint:1')
            self.pps_deblocking_filter_disabled_flag = s.read('uint:1')
            if not self.pps_deblocking_filter_disabled_flag:
                self.pps_beta_offset_div2 = s.read('se')
                self.pps_tc_offset_div2 = s.read('se')
        self.pps_scaling_list_data_present_flag = s.read('uint:1')
        if self.pps_scaling_list_data_present_flag:
            raise Exception('scaling_list_data(s)')
        self.lists_modification_present_flag = s.read('uint:1')
        self.log2_parallel_merge_level_minus2 = s.read('ue')
        self.slice_segment_header_extension_present_flag = s.read('uint:1')
        self.pps_extension_present_flag = s.read('uint:1')
        self.pps_range_extension_flag = s.read('uint:1') if self.pps_extension_present_flag else 0
        self.pps_multilayer_extension_flag = s.read('uint:1') if self.pps_extension_present_flag else 0
        self.pps_scc_extension_flag = s.read('uint:1') if self.pps_extension_present_flag else 0
        self.pps_extension_5bits = s.read('uint:5') if self.pps_extension_present_flag else 0

        if self.pps_range_extension_flag:
            raise Exception('pps_range_extension()')
        else:
            self.diff_cu_chroma_qp_offset_depth = 0 #TODO what is the default for this?
        if self.pps_multilayer_extension_flag:
            raise Exception('pps_multilayer_extension() # specified in Annex F')
        if self.pps_scc_extension_flag:
            raise Exception('pps_scc_extension()')
        if self.pps_extension_5bits:
            raise Exception('while (more_rbsp_data()) pps_extension_data_flag')
        # TODO rbsp_trailing_bits()

    def show(self):
        print()
        print(self.t, 'Picture Parameter Set RBSP')
        print(self.t, '==========================')
        print(self.t, 'pps_pic_parameter_set_id', self.pps_pic_parameter_set_id)
        print(self.t, 'pps_seq_parameter_set_id', self.pps_seq_parameter_set_id)
        print(self.t, 'dependent_slice_segments_enabled_flag', self.dependent_slice_segments_enabled_flag)
        print(self.t, 'output_flag_present_flag', self.output_flag_present_flag)
        print(self.t, 'num_extra_slice_header_bits', self.num_extra_slice_header_bits)
        print(self.t, 'sign_data_hiding_enabled_flag', self.sign_data_hiding_enabled_flag)
        print(self.t, 'cabac_init_present_flag', self.cabac_init_present_flag)
        print(self.t, 'num_ref_idx_l0_default_active_minus1', self.num_ref_idx_l0_default_active_minus1)
        print(self.t, 'num_ref_idx_l1_default_active_minus1', self.num_ref_idx_l1_default_active_minus1)
        print(self.t, 'init_qp_minus26', self.init_qp_minus26)
        print(self.t, 'constrained_intra_pred_flag', self.constrained_intra_pred_flag)
        print(self.t, 'transform_skip_enabled_flag', self.transform_skip_enabled_flag)
        print(self.t, 'cu_qp_delta_enabled_flag', self.cu_qp_delta_enabled_flag)
        if self.cu_qp_delta_enabled_flag:
            print(self.t, 'diff_cu_qp_delta_depth', self.diff_cu_qp_delta_depth)
        print(self.t, 'pps_cb_qp_offset', self.pps_cb_qp_offset)
        print(self.t, 'pps_cr_qp_offset', self.pps_cr_qp_offset)
        print(self.t, 'pps_slice_chroma_qp_offsets_present_flag', self.pps_slice_chroma_qp_offsets_present_flag)
        print(self.t, 'weighted_pred_flag', self.weighted_pred_flag)
        print(self.t, 'weighted_bipred_flag', self.weighted_bipred_flag)
        print(self.t, 'transquant_bypass_enabled_flag', self.transquant_bypass_enabled_flag)
        print(self.t, 'tiles_enabled_flag', self.tiles_enabled_flag)
        print(self.t, 'entropy_coding_sync_enabled_flag', self.entropy_coding_sync_enabled_flag)

        if self.tiles_enabled_flag:
            print(self.t, 'num_tile_columns_minus1', self.num_tile_columns_minus1)
            print(self.t, 'num_tile_rows_minus1', self.num_tile_rows_minus1)
            print(self.t, 'uniform_spacing_flag', self.uniform_spacing_flag)
            if not self.uniform_spacing_flag:
                print(self.t, 'column_width_minus1', self.column_width_minus1)
                print(self.t, 'row_height_minus1', self.row_height_minus1)
            print(self.t, 'loop_filter_across_tiles_enabled_flagi', self.loop_filter_across_tiles_enabled_flagi)
        print(self.t, 'pps_loop_filter_across_slices_enabled_flag', self.pps_loop_filter_across_slices_enabled_flag)
        print(self.t, 'deblocking_filter_control_present_flag', self.deblocking_filter_control_present_flag)
        if self.deblocking_filter_control_present_flag:
            print(self.t, 'deblocking_filter_override_enabled_flag', self.deblocking_filter_override_enabled_flag)
            print(self.t, 'pps_deblocking_filter_disabled_flag', self.pps_deblocking_filter_disabled_flag)
            if not self.pps_deblocking_filter_disabled_flag:
                print(self.t, 'pps_beta_offset_div2', self.pps_beta_offset_div2)
                print(self.t, 'pps_tc_offset_div2', self.pps_tc_offset_div2)
        print(self.t, 'pps_scaling_list_data_present_flag', self.pps_scaling_list_data_present_flag)
        if self.pps_scaling_list_data_present_flag:
            scaling_list_data(s)
        print(self.t, 'lists_modification_present_flag', self.lists_modification_present_flag)
        print(self.t, 'log2_parallel_merge_level_minus2', self.log2_parallel_merge_level_minus2)
        print(self.t, 'slice_segment_header_extension_present_flag', self.slice_segment_header_extension_present_flag)
        print(self.t, 'pps_extension_present_flag', self.pps_extension_present_flag)
        return self

class nal_unit_header(object):
    def __init__(self, s):
        """
        Interpret next bits in BitString s as a nal_unit
        """
        self.forbidden_zero_bit  = s.read('uint:1')
        self.nal_unit_type = s.read('uint:6')
        self.nuh_layer_id = s.read('uint:6')
        self.nuh_temporal_id_plus1 = s.read('uint:3')

    def show(self):
        print('forbidden_zero_bit', self.forbidden_zero_bit)
        print('nal_unit_type', self.nal_unit_type, nal_names[self.nal_unit_type])
        print('nuh_layer_id', self.nuh_layer_id)
        print('nuh_temporal_id_plus1', self.nuh_temporal_id_plus1)
        return self


def read_nal_unit(s, i, NumBytesInNalUnit, state):
    """
    Table 7-1 - NAL unit type codes and NAL unit type classes
    """
    # Advance pointer and skip 24 bits, i.e. 0x000001
    s.pos = i + 24

    n = nal_unit_header(s)
    n.show()

    # 7.3.1.1
    # Convert NAL data (Annex B format) to RBSP data
    NumBytesInRbsp = 0
    rbsp_byte = BitStream()

    for i in range(NumBytesInNalUnit // 8):
        #BH if (i+2) < NumBytesInNalUnit and s.peek('bits:24') == '0x000003':
        if len(s) - s.pos >= 24 and (i+2) < NumBytesInNalUnit and s.peek('bits:24') == '0x000003':
            rbsp_byte.append(s.read('bits:8'))
            rbsp_byte.append(s.read('bits:8'))
            # emulation_prevention_three_byte
            s.read('bits:8')
        elif len(s) - s.pos >= 8:
            #print len(s) - s.pos
            rbsp_byte.append(s.read('bits:8'))
        else:
            raise Exception('not aligned')

    NumBytesInRbsp = len(rbsp_byte)
    s = rbsp_byte

    nal_unit_type = n.nal_unit_type

    if nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_TRAIL_N or \
       nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_TRAIL_R:
        # Coded slice segment of a non-TSA, non-STSA trailing picture
        return slice_segment_layer_rbsp(state, n, s).show()
    elif nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_TSA_N or \
         nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_TSA_R:
        # Coded slice segment of a TSA picture
        return slice_segment_layer_rbsp(state, n, s)
    elif nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_STSA_N or \
         nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_STSA_R:
        # Coded slice segment of an STSA picture
        return slice_segment_layer_rbsp(state, n, s).show()
    elif nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_RADL_N or \
         nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_RADL_R:
        # Coded slice segment of a RADL picture
        return slice_segment_layer_rbsp(state, n, s).show()
    elif nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_RASL_N or \
         nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_RASL_R:
        # Coded slice segment of a RADL picture
        return slice_segment_layer_rbsp(state, n, s).show()
    elif nal_unit_type == NalUnitType.NAL_UNIT_RESERVED_VCL_N10 or \
         nal_unit_type == NalUnitType.NAL_UNIT_RESERVED_VCL_N12 or \
         nal_unit_type == NalUnitType.NAL_UNIT_RESERVED_VCL_N14:
        # Reserved non-IRAP sub-layer non-reference VCL NAL unit types
        return None
    elif nal_unit_type == NalUnitType.NAL_UNIT_RESERVED_VCL_R11 or \
         nal_unit_type == NalUnitType.NAL_UNIT_RESERVED_VCL_R13 or \
         nal_unit_type == NalUnitType.NAL_UNIT_RESERVED_VCL_R15:
        # Reserved non-IRAP sub-layer reference VCL NAL unit types
        return None
    elif nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_BLA_W_LP or \
         nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_BLA_W_RADL or \
         nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_BLA_N_LP:
        # Coded slice segment of a BLA picture
        return slice_segment_layer_rbsp(state, n, s).show()
    elif nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_IDR_W_RADL or \
         nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_IDR_N_LP:
        # Coded slice segment of an IDR picture
        return slice_segment_layer_rbsp(state, n, s).show()
    elif nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_CRA:
        # Coded slice segment of a CRA picture
        return slice_segment_layer_rbsp(state, n, s).show()
    elif nal_unit_type == NalUnitType.NAL_UNIT_RESERVED_IRAP_VCL22 or \
         nal_unit_type == NalUnitType.NAL_UNIT_RESERVED_IRAP_VCL23:
        # Reserved IRAP VCL NAL unit types
        return None
    elif nal_unit_type == NalUnitType.NAL_UNIT_RESERVED_VCL24 or \
         nal_unit_type == NalUnitType.NAL_UNIT_RESERVED_VCL25 or \
         nal_unit_type == NalUnitType.NAL_UNIT_RESERVED_VCL26 or \
         nal_unit_type == NalUnitType.NAL_UNIT_RESERVED_VCL27 or \
         nal_unit_type == NalUnitType.NAL_UNIT_RESERVED_VCL28 or \
         nal_unit_type == NalUnitType.NAL_UNIT_RESERVED_VCL29 or \
         nal_unit_type == NalUnitType.NAL_UNIT_RESERVED_VCL30 or \
         nal_unit_type == NalUnitType.NAL_UNIT_RESERVED_VCL31:
        #Reserved non-IRAP VCL NAL unit types
        return None
    elif nal_unit_type == NalUnitType.NAL_UNIT_VPS:
        # Video parameter set
        print('Found VPS')
        return video_parameter_set_rbsp(s).show()
    elif nal_unit_type == NalUnitType.NAL_UNIT_SPS:
        # Sequence parameter set
        state['sps'] = seq_parameter_set_rbsp(s)
        return state['sps'].show()
    elif nal_unit_type == NalUnitType.NAL_UNIT_PPS:
        # Picture parameter set
        state['pic'] = pic_parameter_set_rbsp(s)
        return state['pic'].show()


def copy_tile(s, state, segments, source, dest):
    tiles = state['pic'].num_tile_rows * state['pic'].num_tile_columns
    frames = [segments[i:i + tiles] for i in range(0, len(segments), tiles)]

    for frame in frames:
        clone = frame[source].clone()
        clone.header.slice_segment_address = frame[dest].header.slice_segment_address
        c = s.replace(frame[dest].bits, clone.bits, count=1, bytealigned=True)
        assert(len(frame) == tiles)
        print(c)
        assert(c == 1)


def extract_nalus_annexb(data):
    """
    Extract NALUs from Annex B byte stream (start code prefixed: 0x000001 or 0x00000001).
    Returns list of (offset, nalu_bytes) tuples.
    """
    nalus = []
    i = 0
    data_len = len(data)

    while i < data_len - 3:
        # Look for start code
        if data[i:i+3] == b'\x00\x00\x01':
            start_code_len = 3
            nalu_start = i + 3
        elif i < data_len - 4 and data[i:i+4] == b'\x00\x00\x00\x01':
            start_code_len = 4
            nalu_start = i + 4
        else:
            i += 1
            continue

        # Find next start code or end of data
        j = nalu_start
        while j < data_len - 3:
            if data[j:j+3] == b'\x00\x00\x01' or (j < data_len - 4 and data[j:j+4] == b'\x00\x00\x00\x01'):
                break
            j += 1
        else:
            j = data_len

        nalu_bytes = data[nalu_start:j]
        if len(nalu_bytes) > 0:
            nalus.append((i, nalu_bytes))

        i = j

    return nalus


def extract_nalus_hvcc(data, nalu_length_size=4):
    """
    Extract NALUs from HVCC/MP4 format (length-prefixed NALUs).
    Returns list of (offset, nalu_bytes) tuples.
    """
    nalus = []
    i = 0
    data_len = len(data)

    while i + nalu_length_size <= data_len:
        # Read NALU length (big-endian)
        nalu_len = int.from_bytes(data[i:i+nalu_length_size], 'big')

        nalu_start = i + nalu_length_size
        nalu_end = nalu_start + nalu_len

        if nalu_end > data_len:
            print(f"Warning: NALU at offset {i} claims length {nalu_len} but only {data_len - nalu_start} bytes remain")
            break

        nalu_bytes = data[nalu_start:nalu_end]
        if len(nalu_bytes) > 0:
            nalus.append((i, nalu_bytes))

        i = nalu_end

    return nalus


def detect_nalu_format(data):
    """
    Detect whether data is in Annex B or HVCC format.
    Returns 'annexb', 'hvcc', or 'unknown'.
    """
    if len(data) < 4:
        return 'unknown'

    # Check for Annex B start codes
    if data[:3] == b'\x00\x00\x01' or data[:4] == b'\x00\x00\x00\x01':
        return 'annexb'

    # Try to interpret as HVCC (4-byte length prefix)
    # If the length makes sense, it's likely HVCC
    nalu_len = int.from_bytes(data[:4], 'big')
    if 0 < nalu_len < len(data) - 4:
        # Check if this looks like a valid NALU header
        if len(data) > 4:
            nal_unit_type = (data[4] >> 1) & 0x3F
            if 0 <= nal_unit_type <= 63:
                return 'hvcc'

    return 'unknown'


def parse_hvcc_extradata(extradata):
    """
    Parse HVCC extradata to extract VPS, SPS, PPS NALUs.
    Returns list of NALU bytes.
    """
    nalus = []

    if len(extradata) < 23:
        return nalus

    # HEVCDecoderConfigurationRecord structure
    # Skip first 21 bytes of config
    # Byte 21: lengthSizeMinusOne (bits 0-1) + other flags
    length_size_minus_one = extradata[21] & 0x03
    nalu_length_size = length_size_minus_one + 1

    # Byte 22: numOfArrays
    num_arrays = extradata[22]

    pos = 23
    for _ in range(num_arrays):
        if pos + 3 > len(extradata):
            break

        # array_completeness (1 bit) + reserved (1 bit) + NAL_unit_type (6 bits)
        # nal_type = extradata[pos] & 0x3F
        num_nalus = int.from_bytes(extradata[pos+1:pos+3], 'big')
        pos += 3

        for _ in range(num_nalus):
            if pos + 2 > len(extradata):
                break

            nalu_len = int.from_bytes(extradata[pos:pos+2], 'big')
            pos += 2

            if pos + nalu_len > len(extradata):
                break

            nalu_bytes = extradata[pos:pos+nalu_len]
            nalus.append(nalu_bytes)
            pos += nalu_len

    return nalus, nalu_length_size


def process_nalu(nalu_bytes, state):
    """
    Process a single NALU and return the parsed result.
    """
    if len(nalu_bytes) < 2:
        return None

    # Create BitStream from NALU bytes
    s = BitStream(bytes=nalu_bytes)

    # Read NAL unit header
    n = nal_unit_header(s)

    # Convert to RBSP (remove emulation prevention bytes)
    rbsp_byte = BitStream()
    s.pos = 16  # Skip NAL header (2 bytes)

    while s.pos < len(s):
        remaining_bits = len(s) - s.pos
        if remaining_bits >= 24 and s.peek('bits:24') == '0x000003':
            rbsp_byte.append(s.read('bits:8'))
            rbsp_byte.append(s.read('bits:8'))
            s.read('bits:8')  # Skip emulation prevention byte
        elif remaining_bits >= 8:
            rbsp_byte.append(s.read('bits:8'))
        else:
            break

    s = rbsp_byte
    nal_unit_type = n.nal_unit_type

    # Process based on NAL unit type
    if nal_unit_type == NalUnitType.NAL_UNIT_VPS:
        print(f'  -> VPS (NAL type {nal_unit_type})')
        try:
            return video_parameter_set_rbsp(s)
        except Exception as e:
            print(f'     Error parsing VPS: {e}')
            return None
    elif nal_unit_type == NalUnitType.NAL_UNIT_SPS:
        print(f'  -> SPS (NAL type {nal_unit_type})')
        try:
            state['sps'] = seq_parameter_set_rbsp(s)
            return state['sps']
        except Exception as e:
            print(f'     Error parsing SPS: {e}')
            return None
    elif nal_unit_type == NalUnitType.NAL_UNIT_PPS:
        print(f'  -> PPS (NAL type {nal_unit_type})')
        try:
            state['pic'] = pic_parameter_set_rbsp(s)
            return state['pic']
        except Exception as e:
            print(f'     Error parsing PPS: {e}')
            return None
    elif nal_unit_type in (NalUnitType.NAL_UNIT_CODED_SLICE_IDR_W_RADL,
                           NalUnitType.NAL_UNIT_CODED_SLICE_IDR_N_LP):
        print(f'  -> IDR slice (NAL type {nal_unit_type})')
        return None  # Skip slice parsing for now
    elif nal_unit_type == NalUnitType.NAL_UNIT_CODED_SLICE_CRA:
        print(f'  -> CRA slice (NAL type {nal_unit_type})')
        return None
    elif nal_unit_type in (NalUnitType.NAL_UNIT_CODED_SLICE_TRAIL_N,
                           NalUnitType.NAL_UNIT_CODED_SLICE_TRAIL_R):
        print(f'  -> Trail slice (NAL type {nal_unit_type})')
        return None
    elif nal_unit_type == NalUnitType.NAL_UNIT_PREFIX_SEI:
        print(f'  -> SEI prefix (NAL type {nal_unit_type})')
        return None
    elif nal_unit_type == NalUnitType.NAL_UNIT_SUFFIX_SEI:
        print(f'  -> SEI suffix (NAL type {nal_unit_type})')
        return None
    elif nal_unit_type == NalUnitType.NAL_UNIT_ACCESS_UNIT_DELIMITER:
        print(f'  -> AUD (NAL type {nal_unit_type})')
        return None
    else:
        print(f'  -> NAL type {nal_unit_type} ({nal_names.get(nal_unit_type, "unknown")})')
        return None


def analyze(filename, max_packets=10):
    """
    Analyze H.265 video using PyAV to load from container.
    Handles both MP4 (HVCC) and Annex B formats.
    """
    print(f'Opening {filename}')

    container = av.open(filename)

    # Find H.265 video stream
    video_stream = None
    for stream in container.streams:
        if stream.type == 'video' and stream.codec_context.name in ('hevc', 'h265'):
            video_stream = stream
            break

    if video_stream is None:
        print('No H.265 video stream found')
        container.close()
        return

    print(f'Found H.265 stream: {video_stream.codec_context.width}x{video_stream.codec_context.height}')
    print(f'Codec: {video_stream.codec_context.name}')

    state = {}
    nalu_length_size = 4  # Default for HVCC

    # Parse extradata (contains VPS, SPS, PPS for HVCC format)
    extradata = video_stream.codec_context.extradata
    if extradata:
        print(f'\nExtradata: {len(extradata)} bytes')

        # Check if it's HVCC format (starts with configurationVersion = 1)
        if len(extradata) > 0 and extradata[0] == 1:
            print('Extradata format: HVCC')
            nalus, nalu_length_size = parse_hvcc_extradata(extradata)
            print(f'NALU length size: {nalu_length_size} bytes')
            print(f'Found {len(nalus)} NALUs in extradata')

            for i, nalu_bytes in enumerate(nalus):
                nal_type = (nalu_bytes[0] >> 1) & 0x3F
                print(f'\n  Extradata NALU {i}: {len(nalu_bytes)} bytes, type={nal_type}')
                process_nalu(nalu_bytes, state)
        else:
            # Try Annex B format in extradata
            print('Extradata format: Annex B (or unknown)')
            nalus = extract_nalus_annexb(bytes(extradata))
            print(f'Found {len(nalus)} NALUs in extradata')

            for i, (offset, nalu_bytes) in enumerate(nalus):
                nal_type = (nalu_bytes[0] >> 1) & 0x3F
                print(f'\n  Extradata NALU {i}: {len(nalu_bytes)} bytes at offset {offset}, type={nal_type}')
                process_nalu(nalu_bytes, state)

    # Process packets
    print(f'\n--- Processing up to {max_packets} packets ---')
    packet_count = 0

    for packet in container.demux(video_stream):
        if packet.dts is None:
            continue

        packet_count += 1
        if packet_count > max_packets:
            break

        packet_bytes = bytes(packet)
        print(f'\nPacket {packet_count}: {len(packet_bytes)} bytes, pts={packet.pts}, dts={packet.dts}, keyframe={packet.is_keyframe}')

        # Detect format and extract NALUs
        fmt = detect_nalu_format(packet_bytes)

        if fmt == 'annexb':
            nalus = extract_nalus_annexb(packet_bytes)
            print(f'  Format: Annex B, {len(nalus)} NALUs')
        else:
            # Assume HVCC format
            nalus = extract_nalus_hvcc(packet_bytes, nalu_length_size)
            print(f'  Format: HVCC (length_size={nalu_length_size}), {len(nalus)} NALUs')

        for j, (offset, nalu_bytes) in enumerate(nalus):
            if len(nalu_bytes) > 0:
                nal_type = (nalu_bytes[0] >> 1) & 0x3F
                print(f'    NALU {j}: {len(nalu_bytes)} bytes, type={nal_type}')
                process_nalu(nalu_bytes, state)

    container.close()
    print(f'\n--- Done. Processed {packet_count} packets ---')


def decode_raw(filename, out_filename):
    """
    Original decode function for raw Annex B H.265 streams.
    """
    print('Reading ' + filename)

    s = BitStream(filename=filename)

    print('Finding NALs')
    nals = list(s.findall('0x000001', bytealigned=True))
    print(f'Found {len(nals)} NALs')
    fudge = 40  # TODO WHY?!?!?!
    sizes = [y - x - fudge for x, y in zip(nals, [*nals[1:], len(s)])]
    state = {}
    rnals = []

    for i, n in zip(nals, sizes):
        print()
        print(f"!! Found NAL @ offset {(i+24)//8:d} ({(i+24)//8:#x}) size {n}")
        rnals.append(read_nal_unit(s, i, n, state))  # bits to bytes

    segments = [seg for seg in rnals if isinstance(seg, slice_segment_layer_rbsp)]
    copy_tile(s, state, segments, 12, 1)
    print([len(seg.body.bits) for seg in segments])

    with open(out_filename, 'wb') as f:
        s.tofile(f)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parse_h265.py <input_file> [max_packets]")
        print("       python parse_h265.py --raw <input.hevc> <output.hevc>")
        sys.exit(1)

    if sys.argv[1] == '--raw':
        # Original raw mode
        if len(sys.argv) < 4:
            print("Usage: python parse_h265.py --raw <input.hevc> <output.hevc>")
            sys.exit(1)
        decode_raw(sys.argv[2], sys.argv[3])
    else:
        # PyAV mode for container formats
        max_packets = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        analyze(sys.argv[1], max_packets)
