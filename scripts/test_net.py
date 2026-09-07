import urllib.request

print(urllib.request.urlopen("http://1.1.1.1", timeout=3).getcode())
