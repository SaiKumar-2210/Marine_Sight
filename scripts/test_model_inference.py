import os
import glob
import time
import torch
import torch.nn as nn
from torchvision import models, transforms
from PIL import Image

# -------------------------------------------------------------
# 1. Rebuild Model Architecture
# -------------------------------------------------------------
def load_trained_model(weights_path, device="cpu"):
    model = models.resnet34(weights=None)
    in_features = model.fc.in_features
    model.fc = nn.Sequential(
        nn.Dropout(0.35),
        nn.Linear(in_features, 1)
    )
    state_dict = torch.load(weights_path, map_location=device)
    model.load_state_dict(state_dict)
    model.to(device)
    model.eval()
    return model

# -------------------------------------------------------------
# 2. Preprocessing Transform (Matching Training)
# -------------------------------------------------------------
val_transforms = transforms.Compose([
    transforms.Grayscale(num_output_channels=3),
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
])

def predict_single_image(model, image_path, device="cpu"):
    img = Image.open(image_path)
    tensor = val_transforms(img).unsqueeze(0).to(device)
    with torch.no_grad():
        start = time.perf_counter()
        logit = model(tensor)
        latency_ms = (time.perf_counter() - start) * 1000
        prob = torch.sigmoid(logit).item()
    return prob, latency_ms

# -------------------------------------------------------------
# 3. Test on Samples from Class_0 and Class_1
# -------------------------------------------------------------
def run_evaluation():
    weights_path = os.path.join("ml_service", "sar_oil_classifier.pth")
    if not os.path.exists(weights_path):
        print(f"Error: {weights_path} not found.")
        return

    print("=" * 65)
    print("       MarineSight ML Model Real-Image Inference Test        ")
    print("=" * 65)
    print(f"Loading trained model from: {weights_path}...")
    model = load_trained_model(weights_path)
    print("Model loaded successfully into evaluation mode.\n")

    # Pick 5 images from Class_0 (No Oil / Look-alikes)
    class_0_files = glob.glob(os.path.join("archive", "kaggle", "data", "Class_0", "*.jpg"))[:5]
    # Pick 5 images from Class_1 (Oil Slicks)
    class_1_files = glob.glob(os.path.join("archive", "kaggle", "data", "Class_1", "*.jpg"))[:5]

    print("--- Testing Class 0: Clean Ocean / Look-alikes (Expected: prob < 0.50) ---")
    c0_correct = 0
    latencies = []
    for f in class_0_files:
        name = os.path.basename(f)
        prob, ms = predict_single_image(model, f)
        latencies.append(ms)
        pred_label = "OIL DETECTED" if prob >= 0.5 else "CLEAN/LOOK-ALIKE"
        status = "[PASS]" if prob < 0.5 else "[FAIL]"
        if prob < 0.5: c0_correct += 1
        print(f"{status} {name[:25]:<25} -> Prob: {prob*100:6.2f}% ({pred_label}) in {ms:.1f}ms")

    print("\n--- Testing Class 1: Real Oil Slicks (Expected: prob >= 0.50) ---")
    c1_correct = 0
    for f in class_1_files:
        name = os.path.basename(f)
        prob, ms = predict_single_image(model, f)
        latencies.append(ms)
        pred_label = "OIL DETECTED" if prob >= 0.5 else "CLEAN/LOOK-ALIKE"
        status = "[PASS]" if prob >= 0.5 else "[FAIL]"
        if prob >= 0.5: c1_correct += 1
        print(f"{status} {name[:25]:<25} -> Prob: {prob*100:6.2f}% ({pred_label}) in {ms:.1f}ms")

    total_tested = len(class_0_files) + len(class_1_files)
    total_correct = c0_correct + c1_correct
    avg_latency = sum(latencies) / len(latencies) if latencies else 0

    print("\n" + "=" * 65)
    print(f"Sample Accuracy : {total_correct}/{total_tested} ({total_correct/total_tested*100:.1f}%)")
    print(f"Class 0 Accuracy: {c0_correct}/{len(class_0_files)} ({c0_correct/len(class_0_files)*100:.1f}%)")
    print(f"Class 1 Accuracy: {c1_correct}/{len(class_1_files)} ({c1_correct/len(class_1_files)*100:.1f}%)")
    print(f"Average Latency : {avg_latency:.1f} ms per tile (on CPU)")
    print("=" * 65)

if __name__ == "__main__":
    run_evaluation()
