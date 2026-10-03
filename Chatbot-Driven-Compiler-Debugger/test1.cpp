#include <iostream>
#include <vector>
#include <algorithm>
using namespace std;

struct Player {
    string name;
    int score;
};
int main() {
    vector<Player> players = {{"Alice", 50}, {"Bob", 70}};
    sort(players.begin(), players.end()); 
    
    return 0;
}