from petri_nets import inter_tpn
from petri_nets import intersections_classes
import time
import paho.mqtt.client as mqtt
import json
import datetime
import numpy as np
from skfuzzy import control as ctrl
import zmq
import sys


# Define the global variable of command_received
intersection_id = "0002"
start_flag = False
msg_dic_dtm = []


# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    global intersection_id
    print("Connected with result code " + str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("intersection/%s/e2det/n" % intersection_id)
    client.subscribe("intersection/%s/e2det/e" % intersection_id)
    client.subscribe("intersection/%s/e2det/s" % intersection_id)
    client.subscribe("intersection/%s/e2det/w" % intersection_id)
    client.subscribe("intersection/all/start")
    print("intersection/%s/e2det/n" % intersection_id)


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    global msg_dic
    global start_flag
    msg.payload = msg.payload.decode("utf-8")
    if msg.topic == "intersection/all/start":
        print("Message %s" % str(msg.payload))
        if "start" in str(msg.payload):
            start_flag = True
        elif "stop" in str(msg.payload):
            start_flag = False
    else:
        msg_dic.append(json.loads(msg.payload))
        print("message arrive from topic: ", msg.topic)
        print(msg.topic + " " + str(msg.payload))


def mqtt_conf() -> mqtt.Client:
    broker_address = "localhost"  # PC Office: "192.168.0.196"; PC Lab: "192.168.5.95"
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(broker_address)  # connect to broker
    return client


def pub_zmq_config():  # Puede que haya que cambiarlo
    port = "5557"
    if len(sys.argv) > 1:
        port = sys.argv[1]
        int(port)

    context = zmq.Context()
    sock = context.socket(zmq.PUB)
    sock.bind("tcp://127.0.0.1:%s" % port)
    time.sleep(0.5)
    return sock


def sub_zmq_config():
    port = "5556"
    if len(sys.argv) > 1:
        port = sys.argv[1]
        int(port)

    context = zmq.Context()
    print("Connecting to server on port %s" % port)
    sock = context.socket(zmq.SUB)
    sock.connect("tcp://127.0.0.1:%s" % port)
    sock.setsockopt(zmq.SUBSCRIBE, b"super/dtm/command")
    sock.setsockopt(zmq.SUBSCRIBE, b"super/dtm/state")
    time.sleep(0.5)
    return sock


def poller_config(socket):
    poller = zmq.Poller()
    if len(socket) > 1:
        for sock in socket:
            poller.register(sock, zmq.POLLIN)
    else:
        poller.register(socket, zmq.POLLIN)
    return poller


def manage_flow(msg_in, movements, moves_detectors, moves_green):
    # Function that saves the detectors and neighbor congestion info
    detector_id = msg_in["id"][-3:]
    print("detector_id: ", detector_id)
    mov_ids = []

    # with open("tpn_state.txt") as f_state:
    #     tpn_state = json.loads(f_state.read())
    #
    # moves_green = [int(i) for i in tpn_state.keys() if tpn_state[i] == "G"]

    print("Detector value changed in lane " + msg_in['laneId'])
    for mov in range(8):  # 8 movements
        if detector_id in moves_detectors[mov]:
            mov_ids.append(mov)
    print("Moves affected: ", mov_ids)
    for mov in mov_ids:
        if mov in moves_green:
            movements[mov].set_jam_length_vehicle(detector_id, msg_in["jamLengthVehicle"])
            movements[mov].set_mean_speed(detector_id, msg_in["meanSpeed"])
        movements[mov].set_occupancy(detector_id, msg_in["occupancy"])
        movements[mov].set_vehicle_number(detector_id, msg_in["vehicleNumber"])

    return


def manage_accidents(msg_in, movements, accident_lanes):
    print("Accident status changes on street " + msg_in['laneId'])
    if msg_in["accidentOnLane"]:
        accident_lanes.append(msg_in['id'])
    else:
        accident_lanes.remove(msg_in['id'])

    acc_mov = []
    if msg_in['id'][24] == "n":
        acc_mov = [3, 6]
    elif msg_in['id'][24] == "e":
        acc_mov = [0, 5]
    elif msg_in['id'][24] == "s":
        acc_mov = [2, 7]
    elif msg_in['id'][24] == "w":
        acc_mov = [1, 4]

    date = datetime.datetime.utcnow().isoformat()
    for mov in acc_mov:
        movements[mov].accident[0] = msg_in["accidentOnLane"]
        movements[mov].accident[1] = date

    accident_msg = {
        "id": msg_in['id'],
        "type": "AccidentObserved",
        "laneId": msg_in['laneId'],
        "location": msg_in['location'],
        "dateObserved": datetime.datetime.utcnow().isoformat(),
        "accidentOnLane": msg_in["accidentOnLane"],  # It has to be configured
        "laneDirection": msg_in['laneDirection']
    }
    return accident_msg


def congestion_model_conf(max_speed, max_vehicle_number):
    # Antecedent/Consequent and universe definition variables
    jamLengthVehicle = ctrl.Antecedent(np.arange(0, max_vehicle_number + 2, 1), 'jamLengthVehicle')
    vehicleNumber = ctrl.Antecedent(np.arange(0, max_vehicle_number + 2, 1), 'vehicleNumber')
    occupancy = ctrl.Antecedent(np.arange(0, 101, 1), 'occupancy')
    meanSpeed = ctrl.Antecedent(np.arange(0, max_speed + 1, 1), 'meanSpeed')
    congestionLevel = ctrl.Consequent(np.arange(0, 101, 1), 'congestionLevel')

    # Membership Functions definition
    jamLengthVehicle.automf(3, 'quant')
    vehicleNumber.automf(3, 'quant')
    occupancy.automf(3, 'quant')
    meanSpeed.automf(3, 'quant')
    congestionLevel.automf(5, 'quant')

    # Graph the Membership Functions
    # jamLengthVehicle.view()
    # vehicleNumber.view()
    # occupancy.view()
    # meanSpeed.view()
    # congestionLevel.view()

    # Define the Expert Rules
    rules = [
        ctrl.Rule((vehicleNumber['high'] | occupancy['high']) & (jamLengthVehicle['high'] | meanSpeed['low']),
                  congestionLevel['higher']),
        ctrl.Rule((vehicleNumber['high'] | occupancy['high']) & (jamLengthVehicle['average'] | meanSpeed['average']),
                  congestionLevel['high']),
        ctrl.Rule((vehicleNumber['high'] | occupancy['high']) & (jamLengthVehicle['low'] | meanSpeed['high']),
                  congestionLevel['average']),
        ctrl.Rule((vehicleNumber['average'] | occupancy['average']) & (jamLengthVehicle['high'] | meanSpeed['low']),
                  congestionLevel['high']),
        ctrl.Rule(
            (vehicleNumber['average'] | occupancy['average']) & (jamLengthVehicle['average'] | meanSpeed['average']),
            congestionLevel['average']),
        ctrl.Rule((vehicleNumber['average'] | occupancy['average']) & (jamLengthVehicle['low'] | meanSpeed['high']),
                  congestionLevel['low']),
        ctrl.Rule((vehicleNumber['low'] | occupancy['low']) & (jamLengthVehicle['high'] | meanSpeed['low']),
                  congestionLevel['average']),
        ctrl.Rule((vehicleNumber['low'] | occupancy['low']) & (jamLengthVehicle['average'] | meanSpeed['average']),
                  congestionLevel['low']),
        ctrl.Rule((vehicleNumber['low'] | occupancy['low']) & (jamLengthVehicle['low'] | meanSpeed['high']),
                  congestionLevel['lower']),
    ]

    # Controller definition
    congestion_model = ctrl.ControlSystem(rules)
    congestion_measuring_sim = ctrl.ControlSystemSimulation(congestion_model)

    return congestion_measuring_sim, congestionLevel


def congestion_measure(congestion_measuring_sim, movement):
    congestion = 0.0
    if movement.get_vehicle_number() != 0:
        congestion_measuring_sim.input['jamLengthVehicle'] = movement.get_jam_length_vehicle()
        congestion_measuring_sim.input['vehicleNumber'] = movement.get_vehicle_number()
        congestion_measuring_sim.input['occupancy'] = movement.get_occupancy()
        congestion_measuring_sim.input['meanSpeed'] = movement.get_mean_speed()
        # Crunch the numbers
        congestion_measuring_sim.compute()
        congestion = congestion_measuring_sim.output['congestionLevel']

    with open("app_%s.log" % intersection_id, "w+") as f:
        f.write(str(movement.get_jam_length_vehicle()) + "; " +
                str(movement.get_vehicle_number()) + "; " +
                str(movement.get_occupancy()) + "; " +
                str(movement.get_mean_speed()) + "; ")

    print("Congestion_", movement.id, " = ", congestion)
    # print("jamLengthVehicle = ", movement.get_jam_length_vehicle(),
    #       "; vehicleNumber = ", movement.get_vehicle_number(),
    #       "; occupancy = ", movement.get_occupancy(),
    #       "; meanSpeed = ", movement.get_mean_speed())
    # congestionLevel.view(sim=congestion_measuring_sim)

    return congestion


def congestion_msg_set(msg_in, mov_cong):
    msg_sup = {
        "id": msg_in["id"],
        "type": msg_in["type"],
        "category": msg_in["category"],
        "data": {
            "value": mov_cong
        }
    }
    return msg_sup


def struct_accident_data_msg(msg_in, mov_acc):
    msg_sup = {
        "id": msg_in["id"],
        "type": msg_in["type"],
        "category": msg_in["category"],
        "data": {
            "value": mov_acc
        }
    }
    return msg_sup


def run():
    global intersection_id
    global start_flag
    global msg_dic_dtm

    super_topic_congestion = b"dtm/state"
    super_topic_accident = b"dtm/state"

    # Setup of the intersection
    inter_info = intersections_classes.Intersection(intersection_id)

    # Setup the congestion model
    congestion_measuring_sim, congestionLevel = congestion_model_conf(inter_info.m_max_speed,
                                                                      inter_info.m_max_vehicle_number)
    # Reset Loop
    accident_lanes = []
    movements = {}  # dictionary of movements
    moves_green = []

    # Create intersection Movements
    for i in range(len(inter_info.movements)):
        if (i == 0) or (inter_info.movements[i] > inter_info.movements[i - 1]):
            movements[inter_info.movements[i]] = intersections_classes.Movement(inter_info.movements[i], inter_info)
    print("Intersection Movements: ", movements)

    print("DTM '%s' READY:" % intersection_id)
    while not start_flag:
        pass  # Do nothing waiting for the start signal

    # Set Petri Net Time, delay and step variables initial values to start
    time_0 = time.perf_counter()
    time_current = 0.0

    # Start the Intersection Petri Net
    print("\n\nStart the Intersection Petri Net:")
    while start_flag:

        # # Add accident in B at t = 30
        # if time_current == 30:
        #     my_accident_change = True
        #     msg_dic.append({
        #         "id": "intersection/0002/e2det/s03",
        #         "type": "AccidentObserved",
        #         "laneId": "436291016#3_3",
        #         "location": "here",
        #         "dateObserved": datetime.datetime.utcnow().isoformat(),
        #         "accidentOnLane": True,
        #         "laneDirection": "s-_wn_"
        #     })
        # # Remove accident in B at t = 300
        # if time_current == 300:
        #     my_accident_change = True
        #     msg_dic.append({
        #         "id": "intersection/0002/e2det/s03",
        #         "type": "AccidentObserved",
        #         "laneId": "436291016#3_3",
        #         "location": "here",
        #         "dateObserved": datetime.datetime.utcnow().isoformat(),
        #         "accidentOnLane": False,
        #         "laneDirection": "s-_wn_"
        #     })

        # Manage msgs received
        if msg_dic:
            msg_mqtt = msg_dic.pop(0)
            msg_id = msg_mqtt['id']
            msg_type = msg_mqtt['type']
            if "e2det" in msg_id:
                if msg_type == "TrafficFlowObserved":
                    manage_flow(msg_mqtt, movements, inter_info.m_detectors, moves_green)
                elif msg_type == "AccidentObserved":
                    # TODO: Create the accident_msg
                    accident_msg = manage_accidents(msg_mqtt, movements, accident_lanes)
                    pub_socket.send_multipart([super_topic_accident, json.dump(accident_msg).encode()])

        # Manage supervisor (zmq) msgs received
        poll = dict(poller.poll(20))
        if sub_socket in poll and poll[sub_socket] == zmq.POLLIN:
            [top, contents] = sub_socket.recv_multipart()
            msg_zmq = json.loads(contents.decode())
            if b"command" in top:
                if "congestion" in msg_zmq["category"]["value"]:
                    msg_movements = list(msg_zmq["value"]["value"])
                    mov_cong = {}
                    for mov in msg_movements:
                        with open("app_%s.log" % intersection_id, "w+") as f:
                            f.write(str(movements[mov].id) + "; " + str(time_current) + "; ")
                        movements[mov].congestionLevel = congestion_measure(congestion_measuring_sim, movements[mov])
                        mov_cong[mov] = movements[mov].congestionLevel
                    cong_data_msg = congestion_msg_set(msg_zmq, mov_cong)
                    pub_socket.send_multipart([super_topic_congestion, json.dumps(cong_data_msg).encode()])
            elif b"state" in top:
                if "display" in msg_zmq["category"]["value"]:
                    msg_display = list(msg_zmq["value"]["value"])
                    moves_green = []
                    for mov in range(len(msg_display)):
                        if msg_display[mov] == "G":
                            moves_green.append(mov)

        # Wait for a second to transit
        time_current += 1.0
        while time.perf_counter() < time_0 + time_current:
            pass


if __name__ == '__main__':
    client_intersection = mqtt_conf()
    # client_intersection: mqtt.Client = mqtt_conf()
    client_intersection.loop_start()  # Necessary to maintain connection
    pub_socket = pub_zmq_config()
    sub_socket = sub_zmq_config()
    poller = poller_config(sub_socket)
    with open("app_%s.log" % intersection_id, "w+") as f:
        f.write("movement_id; time; jam_length_vehicle; vehicle_number; occupancy; mean_speed; my_congestion_level; \n")
    run()