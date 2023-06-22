import yaml

def config_load(path):
    try:
        with open(path) as file:
            obj = yaml.safe_load(file)
            print(obj)
            return obj
    except:
        import traceback
        traceback.print_exc()
        return {}

if __name__ == "__main__":
    config_load('./config.yml')

