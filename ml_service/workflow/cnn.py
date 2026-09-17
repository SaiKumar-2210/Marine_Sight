"""ResNet-34 oil vs look-alike classifier. Scores real SAR chips only."""
from __future__ import annotations

import os
from typing import Optional

import numpy as np
from PIL import Image


class SarOilClassifier:
    def __init__(self, model_dir: str):
        self.model_dir = model_dir
        self.model = None
        self.device = "cpu"
        self.preprocess = None
        self.backend = "unavailable"
        self._load()

    def _load(self) -> None:
        ts_path = os.path.join(self.model_dir, "sar_oil_classifier.pt")
        pth_path = os.path.join(self.model_dir, "sar_oil_classifier.pth")
        try:
            import torch
            from torchvision import transforms

            self.preprocess = transforms.Compose([
                transforms.Grayscale(num_output_channels=3),
                transforms.Resize((224, 224)),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ])
            if os.path.exists(ts_path):
                model = torch.jit.load(ts_path, map_location=self.device)
                model.eval()
                self.model = model
                self.backend = "torchscript"
                return
            if os.path.exists(pth_path):
                import torch.nn as nn
                from torchvision import models

                model = models.resnet34(weights=None)
                model.fc = nn.Sequential(nn.Dropout(0.35), nn.Linear(model.fc.in_features, 1))
                model.load_state_dict(torch.load(pth_path, map_location=self.device))
                model.eval()
                self.model = model
                self.backend = "resnet34-pth"
        except Exception:
            self.model = None
            self.backend = "unavailable"

    def predict_oil_probability(self, image: Image.Image) -> Optional[float]:
        if self.model is None or self.preprocess is None:
            return None
        import torch

        tensor = self.preprocess(image.convert("RGB")).unsqueeze(0)
        with torch.no_grad():
            logit = self.model(tensor)
            if logit.ndim > 1:
                logit = logit.reshape(-1)[0]
            prob = torch.sigmoid(logit).item()
        return float(min(1.0, max(0.0, prob)))

    def predict_array(self, arr: np.ndarray) -> Optional[float]:
        if arr is None or arr.size == 0:
            return None
        if arr.ndim == 2:
            img = Image.fromarray(arr.astype(np.uint8), mode="L").convert("RGB")
        else:
            img = Image.fromarray(arr.astype(np.uint8), mode="RGB")
        return self.predict_oil_probability(img)
