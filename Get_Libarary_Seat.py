import requests,time,re
from bs4 import BeautifulSoup

pattern = re.compile(r'\d+')

Cookie = input('请输入Cookie:')

headers = {
    'Cookie':Cookie
}

url = "http://rg.lib.xjtu.edu.cn:8010/qseat?sp=north2east"

def cancel_seat(num):
    requests.get("http://rg.lib.xjtu.edu.cn:8010/my/?cancel=1&ri="+str(num),headers=headers)


def update_seat(room,seat):
    update_url = "http://rg.lib.xjtu.edu.cn:8010/updateseat/?kid=" +seat+ "&sp=" + room
    res = requests.get(update_url,headers=headers)
    soup = BeautifulSoup(res.text,'html.parser')
    return(soup.find(class_='alert').text)

def choose_seat(room,seat):
    choose_url = "http://rg.lib.xjtu.edu.cn:8010/seat/?kid=" +seat+ "&sp=" + room
    res = requests.get(choose_url,headers=headers)
    soup = BeautifulSoup(res.text,'html.parser')
    return(soup.find(class_='alert').text)

def get_seat(room,available_seats):
    room_url = "http://rg.lib.xjtu.edu.cn:8010/qseat?sp=" + room
    message = requests.get(room_url,headers=headers).json()
    seats = message['seat']
    seats.pop('')
    for seat in seats:
        if seats[seat] == 0:
            available_seats.append((room,seat))

def get_my_status():
    my_url = "http://rg.lib.xjtu.edu.cn:8010/my/"
    html = requests.get(my_url,headers=headers).text
    soup = BeautifulSoup(html,'html.parser')
    items = soup.find_all(class_="bs-calltoaction")
    for item in items[:1]:
        print('-----------------------------------')
        info = item.find(class_='cta-contents')
        status = item.find(class_="cta-button")
        print(info.text.strip())
        print(status.text.strip()[:-5])
        code = status.find('a')
        code = re.search(pattern,code['onclick']).group()
        print(code)
        print('-----------------------------------')
        return code

def get_available_seats():
    available_seats = []
    message = requests.get(url,headers=headers).json()

    rooms = message['scount']
    rooms.pop('')

    for room in rooms:
        name = room
        status = rooms[room][1]

        if status > 0 :
            get_seat(name,available_seats)
    return available_seats

def refresh():
    for i in range(60):
        try:
            code = get_my_status()
            time.sleep(1)
            cancel_seat(code)
            time.sleep(1)
            msg = choose_seat(available_seats_list[index-1][0],available_seats_list[index-1][1])
            print()
            print(msg[3:])
        except:
            pass
    print('正在锁定座位...请勿关闭')

flag = 0
while(True):
    if flag == 0 :
        print('正在获取空余座位列表...')
        available_seats_list = get_available_seats()
        count = 1
        for available_seat in available_seats_list:
            print(str(count),'.位置:',available_seat[0],end='    ')
            print('座位号:',available_seat[1])
            count += 1

        if len(available_seats_list) >= 0:
            index = eval(input('请输入选择座位的编号:'))
            msg = choose_seat(available_seats_list[index-1][0],available_seats_list[index-1][1])
            print()
            print(msg[3:])
            if msg[3:].strip() == '抱歉，该座位已被预约':
                print('-----------------------')
                continue
            else :
                flag = 1
                get_my_status()
                print('正在锁定座位...请勿关闭')
        else :
            print('当前无空位!')
    time.sleep(29*60)
    refresh()
        
    


    

 


    


