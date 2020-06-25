from traceback import print_exc



try: __debugload__
except:   
    __debugload__ = True
    
    debug = False
    
    def printdebug(str, msgdebug):
        global debug
        if msgdebug == False:
            print(str)
            return
        
        if msgdebug == True and debug == True:
            print(str)
            return
            
    def setdebug(set):
        global debug
        debug = set;
        