import numpy as np
import cv2 as cv

class MPPalmDet:
    def __init__(self, modelPath, nmsThreshold=0.3, scoreThreshold=0.5, topK=5000, backendId=0, targetId=0):
        self.model_path = modelPath
        self.nms_threshold = nmsThreshold
        self.score_threshold = scoreThreshold
        self.topK = topK
        self.backend_id = backendId
        self.target_id = targetId

        self.input_size = np.array([192, 192]) # wh

        self.model = cv.dnn.readNet(self.model_path)
        self.model.setPreferableBackend(self.backend_id)
        self.model.setPreferableTarget(self.target_id)

        self.anchors = self._load_anchors()

    @property
    def name(self):
        return self.__class__.__name__

    def setBackendAndTarget(self, backendId, targetId):
        self.backend_id = backendId
        self.target_id = targetId
        self.model.setPreferableBackend(self.backend_id)
        self.model.setPreferableTarget(self.target_id)

    def _preprocess(self, image):
        pad_bias = np.array([0., 0.]) # left, top
        ratio = min(self.input_size / image.shape[:2])
        if image.shape[0] != self.input_size[0] or image.shape[1] != self.input_size[1]:
            # keep aspect ratio when resize
            ratio_size = (np.array(image.shape[:2]) * ratio).astype(np.int32)
            image = cv.resize(image, (ratio_size[1], ratio_size[0]))
            pad_h = self.input_size[0] - ratio_size[0]
            pad_w = self.input_size[1] - ratio_size[1]
            pad_bias[0] = left = pad_w // 2
            pad_bias[1] = top = pad_h // 2
            right = pad_w - left
            bottom = pad_h - top
            image = cv.copyMakeBorder(image, top, bottom, left, right, cv.BORDER_CONSTANT, None, (0, 0, 0))
        image = cv.cvtColor(image, cv.COLOR_BGR2RGB)
        image = image.astype(np.float32) / 255.0 # norm
        pad_bias = (pad_bias / ratio).astype(np.int32)
        return image[np.newaxis, :, :, :], pad_bias # hwc -> nhwc

    def infer(self, image):
        h, w, _ = image.shape

        # Preprocess
        input_blob, pad_bias = self._preprocess(image)

        # Forward
        self.model.setInput(input_blob)
        output_blob = self.model.forward(self.model.getUnconnectedOutLayersNames())

        # Postprocess
        results = self._postprocess(output_blob, np.array([w, h]), pad_bias)

        return results

    def _postprocess(self, output_blob, original_shape, pad_bias):
        score = output_blob[1][0, :, 0]
        box_delta = output_blob[0][0, :, 0:4]
        landmark_delta = output_blob[0][0, :, 4:]
        scale = max(original_shape)

        # get scores
        score = score.astype(np.float64)
        score = 1 / (1 + np.exp(-score))

        # get boxes
        cxy_delta = box_delta[:, :2] / self.input_size
        wh_delta = box_delta[:, 2:] / self.input_size
        xy1 = (cxy_delta - wh_delta / 2 + self.anchors) * scale
        xy2 = (cxy_delta + wh_delta / 2 + self.anchors) * scale
        boxes = np.concatenate([xy1, xy2], axis=1)
        boxes -= [pad_bias[0], pad_bias[1], pad_bias[0], pad_bias[1]]
        # NMS
        keep_idx = cv.dnn.NMSBoxes(boxes, score, self.score_threshold, self.nms_threshold, top_k=self.topK)
        if len(keep_idx) == 0:
            return np.empty(shape=(0, 19))
        selected_score = score[keep_idx]
        selected_box = boxes[keep_idx]

        # get landmarks
        selected_landmarks = landmark_delta[keep_idx].reshape(-1, 7, 2)
        selected_landmarks = selected_landmarks / self.input_size
        selected_anchors = self.anchors[keep_idx]
        for idx, landmark in enumerate(selected_landmarks):
            landmark += selected_anchors[idx]
        selected_landmarks *= scale
        selected_landmarks -= pad_bias

        # [
        #   [bbox_coords, landmarks_coords, score]
        #   ...
        #   [bbox_coords, landmarks_coords, score]
        # ]
        return np.c_[selected_box.reshape(-1, 4), selected_landmarks.reshape(-1, 14), selected_score.reshape(-1, 1)]

    def _load_anchors(self):
        """The SSD anchor grid the palm detector was trained with: 24x24 cells with 2 anchors each,
        then 12x12 with 6, every anchor at its cell's centre. Verified identical to the table the
        upstream file carried literally (max difference 7.5e-09, which is float32 rounding)."""
        rows = []
        for grid, per in ((24, 2), (12, 6)):
            for j in range(grid):
                for i in range(grid):
                    rows += [[(i + 0.5) / grid, (j + 0.5) / grid]] * per
        return np.array(rows, dtype=np.float32)
