#################################
#Author: Theodoros Tsourdinis
#################################

from json import load
import requests
from kubernetes import client,config,watch
import subprocess
import time
import threading
from operator import itemgetter
import csv


PROMETHEUS = 'http://10.64.94.81:32288/'

RTT_THRESHOLD = 10
active_pod_migration = 0
active_vm_migration = 0

MEC_APP_NAME = "video"
VM_MIGRATION_PATH = "/home/ubuntu/oai-mec/follow_me_5g/deploy/follow_me_5g/slicing-core-deployment"
master_node_ip = "10.64.94.81"
pod_migration_times= []
vm_migration_times = []




nodes = {"10.64.45.45": {"name":"station01-b205", "cpu":0, "mem":0, "load":0, "mec":{"status":0,"name":"","rtt":0}, "vms":""}, "10.64.45.46": {"name":"station02-b205", "cpu":0, "mem":0, "load":0, "mec":{"status":0,"name":"","rtt":0}, "vms":""}, "10.64.45.220": {"name":"station03-b205", "cpu":0, "mem":0,"load":0, "mec":{"status":0,"name":"","rtt":0},"vms":""}}
nodes_to_ips = {"station01-b205":"10.64.45.45", "station02-b205":"10.64.45.46", "station03-b205":"10.64.45.220"}

def cpu_per_node(time='60s'):
    response = requests.get(PROMETHEUS + '/api/v1/query', params={'query': f'100 * avg by (instance) (rate(node_cpu_seconds_total\u007bmode!="idle"\u007d[{time}]))'})
    results = response.json()['data']['result']
    for result in results:
        ip = result['metric']['instance'].split(':')[0]
        if ip == master_node_ip:
            continue
        value = float(result['value'][1])/100
        nodes[ip]["cpu"] = value


def mem_per_node(time='20s'):
    response = requests.get(PROMETHEUS + '/api/v1/query', params={'query': f'100 * (1 - ((avg_over_time(node_memory_MemFree_bytes[{time}]) + avg_over_time(node_memory_Cached_bytes[{time}]) + avg_over_time(node_memory_Buffers_bytes[{time}])) / avg_over_time(node_memory_MemTotal_bytes[{time}])))'})
    results = response.json()['data']['result']
    for result in results:
        ip = result['metric']['instance'].split(':')[0]
        if ip == master_node_ip:
            continue
        value = float(result['value'][1])/100
        nodes[ip]["mem"] = value


def rtt_per_node(time="5s", node_name=""):
    if node_name == "station01-b205":
        response = requests.get(PROMETHEUS + '/api/v1/query', params={'query': f'avg_over_time(rtt_node01[{time}])'})
    elif node_name == "station02-b205":
        response = requests.get(PROMETHEUS + '/api/v1/query', params={'query': f'avg_over_time(rtt_node02[{time}])'})
    elif node_name == "station03-b205":
        response = requests.get(PROMETHEUS + '/api/v1/query', params={'query': f'avg_over_time(rtt_node03[{time}])'})

    else:
        return(-1)    

    results = response.json()['data']['result']
    for result in results:
        value = float(result['value'][1])
    nodes[nodes_to_ips[node_name]]["mec"]["rtt"] = value



def mec_source_host(mec_name=MEC_APP_NAME, ns="default"):
    config.load_kube_config()
    v1 = client.CoreV1Api()
    ret = v1.list_pod_for_all_namespaces(watch=False)
    for i in ret.items:
        if (i.metadata.name == mec_name or mec_name in i.metadata.name) and (i.status.phase == 'Running'):
            return i.status.host_ip, i.metadata.name
        else:
            continue
    return(0,0)


def mec_to_host_name_n_status_changer(mec_name=MEC_APP_NAME, ns="default"):
    host_ip,pod_name = mec_source_host(mec_name,ns)
    for key in nodes:
        if key != str(host_ip):  
            nodes[key]["mec"]["name"] = ""
            nodes[key]["mec"]["status"] = 0
        else:
             nodes[host_ip]["mec"]["name"] = pod_name
             nodes[host_ip]["mec"]["status"] = 1
             

def pod_migrate(mec_name=MEC_APP_NAME, ns="default", target_node=""):
    host_ip,pod_name = mec_source_host(mec_name,ns)
    if host_ip == 0 or pod_name == 0:
        print("No mec-app running on any host")
        return 0
    if (host_ip == nodes_to_ips[target_node]):
        print('Migration Error: The source node is same us the target node')
        return 0
    result = subprocess.check_output(['kubectl', 'migrate', pod_name, target_node])
    if "response Status: 200 OK" in str(result):
        start = time.time()
        active_pod_migration = 1
        while(host_ip != nodes_to_ips[target_node]):
            host_ip,pod_name = mec_source_host(MEC_APP_NAME,ns)
        end = time.time()
        active_pod_migration = 0
        migration_time = end-start    
        return migration_time
    else: 
        return 0


def get_vm_host(vm_name="oai-amf"):
    result = subprocess.check_output(['kubectl', 'get', 'vmi'])
    if (str(result) == 'No resources found in default namespace.'):
        print(f"There's no VMI running with name: {vm_name}")
        return 0
    else:
        result = str(result)
        info = result.split(f"{vm_name}")[1]
        node = info.split("True")[0].split("   ")[-2]
        return(node)

def vm_to_host_name_changer(vm_name="oai-amf"):
    node = get_vm_host(vm_name)
    host_ip = nodes_to_ips[node]
    for key in nodes:
        if key != str(host_ip):  
            nodes[key]["vms"] = ""
        else:
             nodes[host_ip]["vms"] = vm_name


def vm_migrate(vm_name="oai-amf", target_node = "", migration_file = "amf_migration.yaml"):  
    source_node = get_vm_host(vm_name)
    if source_node == 0:
        print("There is no such VM to migrate")
        return(-1)

    if (source_node == target_node):
        print('Migration Error: The source node is same us the target node')
        return (-1)

    prepare_target_migration(target_node)    

    #Delete Previous Migration Jobs    
    try:
        result = subprocess.check_call(['kubectl', 'delete', '-f',f'{VM_MIGRATION_PATH}/{migration_file}'])
    except:
        pass
    #Create live migration job for the coressponding Core Network Function
    result = subprocess.check_output(['kubectl', 'create', '-f', f'{VM_MIGRATION_PATH}/{migration_file}'])
    if "created" in str(result):
        print("going for migration")
        start = time.time()
        active_vm_migration = 1
        target_node = get_vm_host(vm_name)
        while(source_node == target_node):
            target_node = get_vm_host(vm_name)
            print(f"source:{source_node} and target: {target_node}")
        end = time.time()
        active_vm_migration = 0
        migration_time = end-start 
        return migration_time
    else:
        print("Migration Failed")
        return(-1)    


def init_node_labels():
    for key in nodes:
        try:
            #Un-label the node:
            result = subprocess.check_call(['kubectl', 'label', 'nodes', nodes[key]["name"], "migration-" ])
        except:
            pass

        try:
            #Label the node:
            result = subprocess.check_call(['kubectl', 'label', 'nodes', nodes[key]["name"], "migration=true" ])
        except:
            pass


def prepare_target_migration(target_node):
    host_ip = nodes_to_ips[target_node]
    for key in nodes:
        if key != str(host_ip):  
            try:
                #Un-label:
                result = subprocess.check_call(['kubectl', 'label', 'nodes', nodes[key]["name"], "migration-" ])
            except:
                pass    
        else:
            try:
                #Un-label:
                result = subprocess.check_call(['kubectl', 'label', 'nodes', nodes[key]["name"], "migration-" ])
            except:
                pass

            try:
                #Un-label:
                result = subprocess.check_call(['kubectl', 'label', 'nodes', nodes[key]["name"], "migration=true" ])
            except:
                pass




def total_load_per_node():
    for key in nodes:
        cpu = nodes[key]["cpu"]
        mem = nodes[key]["mem"]
        load = (1/(1-float(cpu))) * (1/(1-float(mem))) 
        nodes[key]["load"] = load


def min_max_load_node_detect():
    loads = []
    for key in nodes:
        loads.append(tuple([nodes[key]["name"], nodes[key]["load"]]))
        max_loaded_node = max(loads,key=itemgetter(1))[0]
        min_loaded_node = min(loads,key=itemgetter(1))[0]
    return min_loaded_node, max_loaded_node


def increasing_order_load_nodes():
        load = []
        for key in nodes:
            load.append(tuple([nodes[key]["name"], nodes[key]["load"]]))
            load.sort(key=itemgetter(1))
        return load



def min_max_rrt_node_detect():
    rtts = []
    for key in nodes:
        rtts.append(tuple([nodes[key]["name"], nodes[key]["mec"]["rtt"]]))
        max_rtt_node = max(rtts,key=itemgetter(1))[0]
        min_rtt_node = min(rtts,key=itemgetter(1))[0]
    return min_rtt_node, max_rtt_node


def increasing_order_rtt_nodes():
        rtts = []
        for key in nodes:
            rtts.append(tuple([nodes[key]["name"], nodes[key]["mec"]["rtt"]]))
            rtts.sort(key=itemgetter(1))
        return rtts


def get_mec_node():
    for key in nodes:
        if nodes[key]["mec"]["status"] == 1:
            return(nodes[key]["name"])
    return 0


def get_vm_node():
    for key in nodes:
        if nodes[key]["vms"] != "":
            return(nodes[key]["name"])
    return 0


def get_mec_app():
    for key in nodes:
        if nodes[key]["mec"]["status"] == 1:
            return(nodes[key]["mec"]["name"])
        
    return 0


def get_vm_nf():
    for key in nodes:
        if nodes[key]["vms"] != "":
            return(nodes[key]["vms"])
    return 0        



def get_the_current_rtt_node():
        for key in nodes:
            if nodes[key]["mec"]["status"] == 1:
                return(nodes[key]["mec"]["rtt"])
            
        return -1


def get_the_current_load_node():
        for key in nodes:
            if nodes[key]["mec"]["status"] == 1:
                return(nodes[key]["load"])
        
        return -1


def get_the_current_vm_load():
    load = 0
    for key in nodes:
        if nodes[key]["vms"] != "":
            load = nodes[key]["load"] 
            return(load)
    return load

# def mec_app_cpu_usage(time = "60s", pod_name = MEC_APP_NAME, ns = "default"):
#     response = requests.get(PROMETHEUS + f'sum (rate (container_cpu_usage_seconds_total\u007bnamespace = {ns}, pod = {pod_name}\u007d[{time}])) by (pod)')
#     results = response.json()['data']['result']
#     print(results)


# def mec_app_mem_usage(time = "60s", pod_name = MEC_APP_NAME, ns = "default"):
#     response = requests.get(PROMETHEUS + f'sum (rate (container_cpu_usage_seconds_total\u007bnamespace = {ns}, container = {pod_name}\u007d[{time}])) by (pod)')
#     results = response.json()['data']['result']
#     print(results)


# def mec_app_load():
#     app_load = 0
#     return app_load


def update_metrics():
    t1 = threading.Thread(target=cpu_per_node, args=())
    t2 = threading.Thread(target=mem_per_node, args=())
    t3 = threading.Thread(target=rtt_per_node, args=("5s","station01-b205"))
    t4 = threading.Thread(target=rtt_per_node, args=("5s","station02-b205"))
    t5 = threading.Thread(target=rtt_per_node, args=("5s","station03-b205"))
    t6 = threading.Thread(target=mec_to_host_name_n_status_changer, args=())
    t7 = threading.Thread(target=vm_to_host_name_changer, args=())
    t1.start()
    t2.start()
    t3.start()
    t4.start()
    t5.start()
    t6.start()
    t7.start()
    t1.join()
    t2.join()
    total_load_per_node()
    t3.join()
    t4.join()
    t5.join()
    t6.join()
    t7.join()


def init_csv(first_file="rtt.csv", second_file = "load.csv", third_file="host_node.csv"):

    header_first_file = ['rtt_node1', 'rtt_node2', 'rtt_node3']
    header_second_file = ['load_node1', 'load_node2', 'load_node3']
    header_third_file = ['nodes_mec', 'nodes_vm3']
    
    with open(first_file, 'w', encoding='UTF8', newline='') as rtt:
        writer = csv.writer(rtt)

        # write the header
        writer.writerow(header_first_file)

    with open(second_file, 'w', encoding='UTF8', newline='') as load:
        writer = csv.writer(load)

        # write the header
        writer.writerow(header_second_file)


    with open(third_file, 'w', encoding='UTF8', newline='') as hosts:
        writer = csv.writer(hosts)

        # write the header
        writer.writerow(header_third_file)


def write_csv(first_file="rtt.csv", second_file = "load.csv", third_file="host_node.csv"):
    
    pod_node = get_mec_node()
    if pod_node == "station01-b205":
        pod_node = 1
    if pod_node == "station02-b205":
        pod_node = 2        
    if pod_node == "station03-b205":
        pod_node = 3

    vm_node = get_vm_node()
    if vm_node == "station01-b205":
        vm_node = 1
    if vm_node == "station02-b205":
        vm_node = 2        
    if vm_node == "station03-b205":
        vm_node = 3  
    
    data_first_file = [nodes[nodes_to_ips["station01-b205"]]["mec"]["rtt"], nodes[nodes_to_ips["station02-b205"]]["mec"]["rtt"], nodes[nodes_to_ips["station03-b205"]]["mec"]["rtt"]]
    data_second_file = [nodes[nodes_to_ips["station01-b205"]]["load"], nodes[nodes_to_ips["station02-b205"]]["load"], nodes[nodes_to_ips["station03-b205"]]["load"]]
    data_third_file = [pod_node, vm_node]

    with open(first_file, 'a', encoding='UTF8', newline='') as rtt:
        writer = csv.writer(rtt)

        # write the data
        writer.writerow(data_first_file)

    with open(second_file, 'a', encoding='UTF8', newline='') as load:
        writer = csv.writer(load)

        # write the data
        writer.writerow(data_second_file)

    with open(third_file, 'a', encoding='UTF8', newline='') as hosts:
        writer = csv.writer(hosts)

        # write the data
        writer.writerow(data_third_file)                



def pod_migration_policy():
    
    while(1):
        optimal_node = 0
        update_metrics()
        current_rtt = get_the_current_rtt_node()
        current_load = get_the_current_load_node()
        source_mec_node = get_mec_node()
        mec_app = get_mec_app()
        good_rtt_better_load_than_current = []
        average_rtt_better_load_than_current = []
        
        #If mec pod exists, check the latency experience and the health of the node that the pod is hosted
        if source_mec_node:
            #Good Experience or Average Experience -> Stay or Migrate desicions depends on the node load
            if  3 <= current_rtt <= 8 or 8 <= current_rtt <= 18:
                #Healthy Status -> Stay    
                if 1 <= current_load <= 5:
                    continue
                #Average Status -> Stay
                if  5 <= current_load <= 10:
                    continue
                #Bad Status -> Check for the optimal target node ASAP
                if 10 <= current_load <= 100:
                    rtt_node_list = increasing_order_rtt_nodes()
                    # Check candidate nodes that have good experience
                    for rtt_node in rtt_node_list:
                        if source_mec_node == rtt_node[0]:
                            continue 
                        # If the candidate node provides good experience -> Check if it has healthy or average load status
                        if 3 <= rtt_node[-1] <= 8:
                            rtt_node_load = nodes[nodes_to_ips[rtt_node[0]]]["load"]
                            if 1 <= rtt_node_load <= 5 or 5 <= rtt_node_load <= 10:
                                #Optimal target node founded
                                optimal_node = 1
                                migration_time = pod_migrate(mec_app,"default", rtt_node[0])
                                pod_migration_times.append(migration_time)
                                time.sleep(35) #Power - Bandwidth Save - Wait before another migration
                                break
                            #Else the candidate node provides good experience but it has bad load status -> Keep the bad loads that are less than the current bad load
                            else:
                                if(current_load > rtt_node_load):
                                    good_rtt_better_load_than_current.append(tuple(rtt_node[0], rtt_node_load))
                        
                    #We didn't find a candidate node with good experience and healthy or average load status -> Lets check nodes with average experience.
                    if optimal_node == 0:
                        for rtt_node in rtt_node_list:
                            if source_mec_node == rtt_node[0]:
                                continue 
                            # If the candidate node provides average experience -> Check if it has healthy or average load status
                            if 8 <= rtt_node[-1] <= 18:
                                rtt_node_load = nodes[nodes_to_ips[rtt_node[0]]]["load"]
                                if 1 <= rtt_node_load <= 5 or 5 <= rtt_node_load <= 10:
                                    #Optimal target node founded
                                    optimal_node = 1
                                    migration_time = pod_migrate(mec_app,"default", rtt_node[0])
                                    pod_migration_times.append(migration_time)
                                    time.sleep(35) #Power - Bandwidth Save - Wait before another migration
                                    break
                                #Else the candidate node provides average experience but it has bad load status -> Keep the bad loads that are less than the current bad load
                                else:
                                    if(current_load > rtt_node_load):
                                        average_rtt_better_load_than_current.append(tuple(rtt_node[0], rtt_node_load))

                        #We didn't find a candidate node with good or average experience and healthy or average load status -> Let's migrate to a node that has the minimum bad load status

                        if optimal_node == 0:
                            #Migrate to a node that has good RTT and the minimum bad load
                            if good_rtt_better_load_than_current:
                                min_rtt_node_related_to_current = min(good_rtt_better_load_than_current,key=itemgetter(1))[0]
                                #Optimal target node founded
                                optimal_node = 1
                                migration_time = pod_migrate(mec_app,"default", min_rtt_node_related_to_current)
                                pod_migration_times.append(migration_time)
                                time.sleep(35) #Power - Bandwidth Save - Wait before another migration
                            #We didn't find a good RTT and the minimum bad load -> So Migrate to a node that has average RTT and the minimum bad load status (Worst-Case)
                            else:
                                if average_rtt_better_load_than_current:
                                    min_rtt_node_related_to_current = min(average_rtt_better_load_than_current,key=itemgetter(1))[0]
                                    #Optimal target node founded
                                    optimal_node = 1
                                    migration_time = pod_migrate(mec_app,"default", min_rtt_node_related_to_current)
                                    pod_migration_times.append(migration_time)  #Power - Bandwidth Save - Wait before another migration
                                    time.sleep(35)
             
            #Bad Experience -> Leave
            if 25 <= current_rtt <= 50:
                    rtt_node_list = increasing_order_rtt_nodes()
                    # Check candidate nodes that have good experience
                    for rtt_node in rtt_node_list:
                        if source_mec_node == rtt_node[0]:
                            continue 
                        # If the candidate node provides good experience -> Check if it has healthy or average load status
                        if 3 <= rtt_node[-1] <= 8:
                            rtt_node_load = nodes[nodes_to_ips[rtt_node[0]]]["load"]
                            if 1 <= rtt_node_load <= 5 or 5 <= rtt_node_load <= 10:
                                #Optimal target node founded
                                optimal_node = 1
                                migration_time = pod_migrate(mec_app,"default", rtt_node[0])
                                pod_migration_times.append(migration_time)
                                #Power - Bandwidth Save - Wait before another migration
                                time.sleep(35)
                                break
                            #Else the candidate node provides good experience but it has bad load status -> Keep the bad loads that are less than the current bad load
                            else:
                                if(current_load > rtt_node_load):
                                    good_rtt_better_load_than_current.append(tuple(rtt_node[0], rtt_node_load))
                    #We didn't find a candidate node with good experience and healthy or average load status -> Lets check nodes with average experience.
                    if optimal_node == 0:
                        for rtt_node in rtt_node_list:
                            if source_mec_node == rtt_node[0]:
                                continue 
                            # If the candidate node provides average experience -> Check if it has healthy or average load status
                            if 8 <= rtt_node[-1] <= 18:
                                rtt_node_load = nodes[nodes_to_ips[rtt_node[0]]]["load"]
                                if 1 <= rtt_node_load <= 5 or 5 <= rtt_node_load <= 10:
                                    #Optimal target node founded
                                    optimal_node = 1
                                    migration_time = pod_migrate(mec_app,"default", rtt_node[0])
                                    pod_migration_times.append(migration_time)
                                    time.sleep(35) #Power - Bandwidth Save - Wait before another migration
                                    break
                                #Else the candidate node provides average experience but it has bad load status -> Keep the bad loads that are less than the current bad load
                                else:
                                    if(current_load > rtt_node_load):
                                        average_rtt_better_load_than_current.append(tuple(rtt_node[0], rtt_node_load))

                        #We didn't find a candidate node with good or average experience and healthy or average load status -> Let's migrate to a node that has the minimum bad load status

                        if optimal_node == 0:
                            #Migrate to a node that has good RTT and the minimum bad load
                            if good_rtt_better_load_than_current:
                                min_rtt_node_related_to_current = min(good_rtt_better_load_than_current,key=itemgetter(1))[0]
                                #Optimal target node founded
                                optimal_node = 1
                                migration_time = pod_migrate(mec_app,"default", min_rtt_node_related_to_current)
                                pod_migration_times.append(migration_time)
                                time.sleep(35) #Power - Bandwidth Save - Wait before another migration
                            #We didn't find a good RTT and the minimum bad load -> So Migrate to a node that has average RTT and the minimum bad load status (Worst-Case)
                            else:
                                if average_rtt_better_load_than_current:
                                    min_rtt_node_related_to_current = min(average_rtt_better_load_than_current,key=itemgetter(1))[0]
                                    #Optimal target node founded
                                    optimal_node = 1
                                    migration_time = pod_migrate(mec_app,"default", min_rtt_node_related_to_current)
                                    pod_migration_times.append(migration_time)  #Power - Bandwidth Save - Wait before another migration
                                    time.sleep(35)                
                  
          
def vm_migration_policy():
    while 1:
        optimal_node = 0
        update_metrics()
        current_load = get_the_current_vm_load()
        current_node = get_vm_host()
        vm_nf = get_vm_nf()
        migration_file = "amf_migration.yaml"
        less_bad_load_than_current = []
        
        if current_node:
            #Healthy Status -> Stay
            if 1 <= current_load <= 5:
                    continue
            #Average Status -> Stay
            if  5 <= current_load <= 10:
                    continue
            #Bad Status -> Check for the optimal target node ASAP
            if 10 <= current_load <= 100:
                load_node_list = increasing_order_load_nodes()
                for load_node in load_node_list:
                    if current_node == load_node[0]:
                        continue
                    #First Check if there are healthy nodes available 
                    if 1 <= load_node[-1] <= 5:
                            #Optimal target node founded
                            optimal_node = 1
                            migration_time = vm_migrate(vm_nf, load_node[0], migration_file)
                            vm_migration_times.append(migration_time)
                            break
            #No healthy node available found -> Check for average load status
            if optimal_node == 0:
                for load_node in load_node_list:
                    if current_node == load_node[0]:
                        continue 
                    if 5 <= load_node[-1] <= 10:
                            #Optimal target node founded
                            optimal_node = 1
                            migration_time = vm_migrate(vm_nf, load_node[0], migration_file)
                            vm_migration_times.append(migration_time)
                            break
                    else:
                        if current_load > load_node[-1]:
                            less_bad_load_than_current.append(load_node[0],load_node[-1])

                #No healthy - No average load status nodes found -> Go to the less bad load node
                if optimal_node == 0:
                    if less_bad_load_than_current:
                        min_bad_load_node = min(less_bad_load_than_current,key=itemgetter(1))[0]
                        optimal_node = 1
                        migration_time = vm_migrate(vm_nf, min_bad_load_node, migration_file)
                        vm_migration_times.append(migration_time)



if __name__ == "__main__":
    t0 = threading.Thread(target=init_node_labels, args=())
    t1 = threading.Thread(target=init_csv, args=())
    t0.start()
    t1.start()
    t0.join()
    t1.join()
    init_csv()
    while 1:
        ### VM Metrics Test
        update_metrics()
        print(nodes)
        print()
        time.sleep(1)

