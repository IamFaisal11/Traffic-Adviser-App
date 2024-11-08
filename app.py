import os
from flask import Flask, render_template, Response, request, redirect, url_for
import cv2
from werkzeug.utils import secure_filename
from ultralytics import YOLO
import numpy as np
import time

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Load YOLOv8 models
yolov8_road_damage = YOLO(r'models/YOLOv8_Small_RDD.pt')  # Model for road damage detection
yolov8_aerial = YOLO(r'models/arielview.pt')               # Model for aerial view object detection
yolov8_traffic_light = YOLO(r'models/best (7).pt')         # Another YOLO model, possibly trained
yolov8_road_object = YOLO(r'models/yolov8n.pt')            # YOLOv8 nano model

# Traffic light class names
traffic_light_names = {0: 'stop', 1: 'go', 2: 'stopLeft', 3: 'warning', 4: 'warningLeft'}

def process_frame(frame, time_between_frames=1/30):
    """
    Processes the input frame through all models and combines their outputs.
    """
    # Define class IDs for cars
    car_class_id = 2  

    output_frame = frame.copy()
    
    # Initialize variables
    road_damage_detected = False
    road_damage_classes = []
    traffic_light_detected = False
    traffic_light_classes = []
    road_object_detected = False
    road_object_classes = []
    vehicle_classes = [2, 5, 7]
    previous_positions = {}
    previous_speeds = {}
    vehicle_id_counter = 0
    distance_threshold = 100  
    far_distance_threshold = 500
    slow_speed_threshold = 500  
    congestion_radius = 200  

    # YOLOv8 Road Damage Detection
    results_rd = yolov8_road_damage(frame)
    if results_rd and len(results_rd) > 0:
        detections_rd = results_rd[0]
        if detections_rd.boxes.cls is not None and len(detections_rd.boxes.cls) > 0:
            road_damage_detected = True
            class_ids = detections_rd.boxes.cls.numpy()
            road_damage_classes = class_ids.tolist()
        annotated_frame_rd = detections_rd.plot()
    else:
        annotated_frame_rd = frame
    
    # YOLOv8 Traffic Light Detection
    results_tl = yolov8_traffic_light(frame)
    if results_tl and len(results_tl) > 0:
        detections_tl = results_tl[0]
        if detections_tl.boxes.cls is not None and len(detections_tl.boxes.cls) > 0:
            traffic_light_detected = True
            class_ids = detections_tl.boxes.cls.numpy()
            traffic_light_classes = class_ids.tolist()
        annotated_frame_tl = detections_tl.plot()
    else:
        annotated_frame_tl = frame
    
    # YOLOv8 Aerial Detection
    results_aerial = yolov8_aerial(frame)
    annotated_frame_aerial = results_aerial[0].plot()
    
    # Perform vehicle detection
    im_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    im_rgb_with_lines2 = im_rgb.copy()

    # Perform inference on the current frame for vehicle detection
    results_vehicle = yolov8_aerial(frame)
    boxes = results_vehicle[0].boxes.xyxy.cpu().numpy()
    scores = results_vehicle[0].boxes.conf.cpu().numpy()
    classes = results_vehicle[0].boxes.cls.cpu().numpy()

    current_frame_vehicle_positions = []
    for i in range(len(boxes)):
        x1, y1, x2, y2 = boxes[i]
        score = scores[i]
        class_id = int(classes[i])

        if class_id in vehicle_classes:
            x_center = int((x1 + x2) / 2)
            y_center = int((y1 + y2) / 2)

            # Match the current position to previous positions
            matched_vehicle_id = None
            min_distance = float('inf')

            for vehicle_id, (prev_x, prev_y) in previous_positions.items():
                distance = np.sqrt((x_center - prev_x) ** 2 + (y_center - prev_y) ** 2)
                if distance < min_distance and distance < distance_threshold:
                    min_distance = distance
                    matched_vehicle_id = vehicle_id

            if matched_vehicle_id is None:
                # New vehicle detected
                vehicle_id_counter += 1
                matched_vehicle_id = vehicle_id_counter

            current_frame_vehicle_positions.append((matched_vehicle_id, (x_center, y_center), class_id))

            # Calculate speed
            if matched_vehicle_id in previous_positions:
                previous_x, previous_y = previous_positions[matched_vehicle_id]
                distance_moved = np.sqrt((x_center - previous_x) ** 2 + (y_center - previous_y) ** 2)
                speed = distance_moved / time_between_frames
            else:
                speed = 0  # No previous position, assume no movement

            # Update the previous position and speed for this vehicle
            previous_positions[matched_vehicle_id] = (x_center, y_center)
            previous_speeds[matched_vehicle_id] = speed

    # Detect congestion
    slow_vehicles = [pos for (vid, pos, cid) in current_frame_vehicle_positions if previous_speeds.get(vid, 0) < slow_speed_threshold]
    congestion_detected = len(slow_vehicles) > 5  # Arbitrary threshold for congestion detection

    # Connect every vehicle with each other and check distances
    for i in range(len(current_frame_vehicle_positions)):
        for j in range(i + 1, len(current_frame_vehicle_positions)):
            vehicle_id1, (x1, y1), class_id1 = current_frame_vehicle_positions[i]
            vehicle_id2, (x2, y2), class_id2 = current_frame_vehicle_positions[j]

            inter_vehicle_distance = np.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)
            cv2.line(im_rgb_with_lines2, (x1, y1), (x2, y2), (0, 255, 255), 2)

            # Check if the vehicles are too close
            if inter_vehicle_distance < distance_threshold:
                cv2.putText(im_rgb_with_lines2, 'Vehicles are close: Keep a lookout!', 
                            (min(x1, x2), min(y1, y2) - 10), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                cv2.line(im_rgb_with_lines2, (x1, y1), (x2, y2), (0, 0, 255), 2)

            # Check if the vehicles are far apart and moving fast, only for cars
            elif inter_vehicle_distance > far_distance_threshold:
                if class_id1 == car_class_id or class_id2 == car_class_id:
                    cv2.putText(im_rgb_with_lines2, 'You are driving fast: Slow down!', 
                                (min(x1, x2), min(y1, y2) - 10), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)
                    cv2.line(im_rgb_with_lines2, (x1, y1), (x2, y2), (255, 0, 0), 2)

    # Congestion Warning
    if congestion_detected:
        for vehicle_id, (x, y) in previous_positions.items():
            if previous_speeds.get(vehicle_id, 0) < slow_speed_threshold:
                cv2.putText(im_rgb_with_lines2, 'Congestion ahead: Slow down or change route!', 
                            (x - 50, y - 20), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

    return im_rgb_with_lines2

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return redirect(request.url)
    file = request.files['file']
    if file.filename == '':
        return redirect(request.url)
    if file:
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        return redirect(url_for('process', filename=filename))

@app.route('/process/<filename>')
def process(filename):
    # Placeholder for processing the uploaded file
    return f'Processing file: {filename}'

if __name__ == '__main__':
    app.run(debug=True)
