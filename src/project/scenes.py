class SceneManager:

    def __init__(self):

        self.markers = []

    def toggle(
            self,
            frame,
    ):

        if frame in self.markers:

            self.markers.remove(
                frame
            )

        else:

            self.markers.append(
                frame
            )

            self.markers.sort()

    def previous(
            self,
            current,
    ):

        if not self.markers:
            return current

        left = [

            m

            for m

            in self.markers

            if m < current

        ]

        if left:
            return max(
                left
            )

        #
        # Wrap
        #

        return max(
            self.markers
        )

    def next(
            self,
            current,
    ):

        if not self.markers:
            return current

        right = [

            m

            for m

            in self.markers

            if m > current

        ]

        if right:
            return min(
                right
            )

        #
        # Wrap
        #

        return min(
            self.markers
        )

    def has_marker(
            self,
            frame,
    ):

        return (
            frame
            in
            self.markers
        )