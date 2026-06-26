class SelectionManager:

    def __init__(self):

        #
        # Temporary markers
        #

        self.pending_in = None
        self.pending_out = None

        #
        # Saved keep ranges
        #

        self.ranges = []
        self.undo_stack = []
        self.redo_stack = []

        # When the user marks IN inside an existing range, that range's index
        # is recorded here so the next commit REPLACES it (adjusts its
        # boundaries) rather than merging into it.  None means "new range".
        self.editing_index = None

    def range_index_at(self, frame):
        """Return the index of the range containing `frame`, or None."""
        for i, (start, end) in enumerate(self.ranges):
            if start <= frame <= end:
                return i
        return None

    def clear_pending(self):

        self.pending_in = None
        self.pending_out = None
        self.editing_index = None

    def clear_all(self):

        self.clear_pending()

        self.ranges.clear()

    def push_undo_state(self):

        self.undo_stack.append(
            list(
                self.ranges
            )
        )

        #
        # New edits invalidate redo
        #

        self.redo_stack.clear()

    def set_in(
            self,
            frame,
    ):

        self.pending_in = frame

    def set_out(
            self,
            frame,
    ):

        self.pending_out = frame

    def commit_range(self):

        if (
            self.pending_in is None
            or
            self.pending_out is None
        ):
            return False

        start = min(
            self.pending_in,
            self.pending_out
        )

        end = max(
            self.pending_in,
            self.pending_out
        )

        self.push_undo_state()

        new_start = start
        new_end = end

        #
        # Decide EDIT vs ADD by whether the new IN..OUT span overlaps any
        # existing scene.  This is more robust than keying off where the IN
        # landed: when adjusting a scene the new IN often sits just before or
        # after the old boundary (so it isn't strictly "inside" the old span),
        # but the intent is still to replace that scene.
        #
        # EDIT (span overlaps existing scenes): the new IN..OUT replaces them
        # exactly - any overlapping scene material, inside or partially inside
        # the span, is discarded.  This matches VideoReDo, where marking IN in
        # one scene and OUT in the next collapses to a single IN..OUT scene.
        #
        # ADD (span overlaps nothing): insert as a new scene, merging only
        # with directly adjacent scenes.
        #
        overlaps = any(
            not (existing_end < new_start or existing_start > new_end)
            for existing_start, existing_end in self.ranges
        )

        if overlaps:
            kept = [
                (existing_start, existing_end)
                for existing_start, existing_end in self.ranges
                if existing_end < new_start or existing_start > new_end
            ]
            kept.append((new_start, new_end))
            kept.sort()
            self.ranges = kept
            self.clear_pending()
            return True

        #
        # ADDING a new range: merge with any directly adjacent ranges.
        #
        merged = []

        for existing_start, existing_end in self.ranges:

            #
            # No overlap
            #

            if (
                    existing_end < new_start - 1
                    or
                    existing_start > new_end + 1
            ):
                merged.append(
                    (
                        existing_start,
                        existing_end,
                    )
                )

                continue

            #
            # Merge overlap
            #

            new_start = min(
                new_start,
                existing_start,
            )

            new_end = max(
                new_end,
                existing_end,
            )

        #
        # Add merged result
        #

        merged.append(
            (
                new_start,
                new_end,
            )
        )

        merged.sort()

        self.ranges = merged

        #
        # Reset temporary markers
        #

        self.clear_pending()

        return True

    def remove_range(
            self,
            index,
    ):

        if (
                index < 0
                or
                index >= len(
            self.ranges
        )
        ):
            return False

        self.push_undo_state()

        del self.ranges[index]

        return True

    def remove_ranges(self, indices):
        """Remove several ranges at once as a single undo step.

        Returns the number actually removed.  Indices are de-duplicated and
        deleted high-to-low so earlier deletions don't shift later ones.
        """
        valid = sorted(
            {i for i in indices if 0 <= i < len(self.ranges)},
            reverse=True,
        )

        if not valid:
            return 0

        self.push_undo_state()

        for i in valid:
            del self.ranges[i]

        return len(valid)

    def undo(self):

        if not self.undo_stack:
            return False

        self.redo_stack.append(
            list(
                self.ranges
            )
        )

        self.ranges = (
            self.undo_stack.pop()
        )

        return True

    def redo(self):

        if not self.redo_stack:
            return False

        self.undo_stack.append(
            list(
                self.ranges
            )
        )

        self.ranges = (
            self.redo_stack.pop()
        )

        return True